"""Password hashing (stdlib PBKDF2) and in-memory bearer-token sessions.

Tokens live in process memory: a restart of the API simply requires the
admin to log in again, which is acceptable for a single-machine POS.
"""

import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Callable

logger = logging.getLogger("lotspot.auth")

PBKDF2_ITERATIONS = 600_000
TOKEN_TTL_HOURS = 12
SESSION_IDLE_TIMEOUT_MINUTES = 15

# PCI DSS v4 Req 8.3.4: lock out after no more than 10 invalid attempts, for
# at least 30 minutes or until an admin resets it. We do not offer an admin
# reset path yet, so the lockout simply expires after the window.
LOGIN_MAX_ATTEMPTS = 10
LOGIN_LOCKOUT_MINUTES = 30

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return f"pbkdf2:sha256:{PBKDF2_ITERATIONS}:{salt}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, algo, iterations, salt, expected = stored.split(":")
        if scheme != "pbkdf2" or algo != "sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


# TODO(security): admin login has no second factor. PCI DSS v4 Req 8.4.2
# will require MFA for all access into the CDE once a card-acceptance tier
# is chosen; that choice gates whether MFA needs new hardware (a PIN pad,
# a TOTP app) or can reuse something already in the stack. Revisit once the
# LotSpot constitution documents that tier decision.


class TokenStore:
    """In-memory bearer tokens with a 12h absolute TTL and a 15-minute idle
    timeout (PCI DSS v4 Req 8.2.8): a token dies at whichever comes first."""

    def __init__(
        self,
        ttl: timedelta = timedelta(hours=TOKEN_TTL_HOURS),
        idle_timeout: timedelta = timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES),
        now: Callable[[], datetime] = datetime.now,
    ):
        self._tokens: dict[str, dict] = {}
        self._ttl = ttl
        self._idle_timeout = idle_timeout
        self._now = now

    def create(self, username: str) -> dict:
        token = secrets.token_urlsafe(32)
        created_at = self._now()
        expires_at = created_at + self._ttl
        self._tokens[token] = {
            "username": username,
            "expires_at": expires_at,
            "last_active": created_at,
        }
        return {"token": token, "expires_at": expires_at.isoformat()}

    def validate(self, token: str) -> str | None:
        """Return the username for a live token, or None. A successful
        validate counts as activity and refreshes the idle window."""
        entry = self._tokens.get(token)
        if entry is None:
            return None
        now = self._now()
        if entry["expires_at"] < now:
            del self._tokens[token]
            return None
        if entry["last_active"] + self._idle_timeout < now:
            del self._tokens[token]
            return None
        entry["last_active"] = now
        return entry["username"]

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)


class LoginRateLimiter:
    """Per-key failed-login throttling (PCI DSS v4 Req 8.3.4).

    Keys are caller-defined strings (e.g. "user:<username>" or "ip:<addr>")
    so the same limiter instance can enforce independent per-username and
    per-IP lockouts. A key locks out after `max_attempts` consecutive
    failures and stays locked for `lockout`; any success clears its state.
    """

    def __init__(
        self,
        max_attempts: int = LOGIN_MAX_ATTEMPTS,
        lockout: timedelta = timedelta(minutes=LOGIN_LOCKOUT_MINUTES),
        now: Callable[[], datetime] = datetime.now,
    ):
        self._max_attempts = max_attempts
        self._lockout = lockout
        self._now = now
        self._state: dict[str, dict] = {}

    def is_locked(self, key: str) -> bool:
        entry = self._state.get(key)
        if entry is None or entry["locked_until"] is None:
            return False
        if entry["locked_until"] <= self._now():
            del self._state[key]
            return False
        return True

    def record_failure(self, key: str) -> None:
        entry = self._state.setdefault(key, {"failures": 0, "locked_until": None})
        entry["failures"] += 1
        if entry["failures"] >= self._max_attempts:
            entry["locked_until"] = self._now() + self._lockout

    def record_success(self, key: str) -> None:
        self._state.pop(key, None)


def seed_admin(conn: sqlite3.Connection, now_iso: str) -> None:
    """Create the initial admin account if no admins exist yet.

    Credentials come from LOTSPOT_ADMIN_USER / LOTSPOT_ADMIN_PASSWORD.
    The defaults are for first-boot convenience on a local POS only.
    """
    count = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if count > 0:
        return

    username = os.environ.get("LOTSPOT_ADMIN_USER", DEFAULT_ADMIN_USER)
    password = os.environ.get("LOTSPOT_ADMIN_PASSWORD")
    if password is None:
        password = DEFAULT_ADMIN_PASSWORD
        logger.warning(
            "Seeded admin user %r with the DEFAULT password. "
            "Set LOTSPOT_ADMIN_PASSWORD before exposing this machine to anyone "
            "but the store operator.",
            username,
        )

    conn.execute(
        "INSERT INTO admin_users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, hash_password(password), now_iso),
    )
    conn.commit()
