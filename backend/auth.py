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

logger = logging.getLogger("lotspot.auth")

PBKDF2_ITERATIONS = 600_000
TOKEN_TTL_HOURS = 12

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


class TokenStore:
    """In-memory bearer tokens with expiry."""

    def __init__(self, ttl: timedelta = timedelta(hours=TOKEN_TTL_HOURS)):
        self._tokens: dict[str, dict] = {}
        self._ttl = ttl

    def create(self, username: str) -> dict:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + self._ttl
        self._tokens[token] = {"username": username, "expires_at": expires_at}
        return {"token": token, "expires_at": expires_at.isoformat()}

    def validate(self, token: str) -> str | None:
        """Return the username for a live token, or None."""
        entry = self._tokens.get(token)
        if entry is None:
            return None
        if entry["expires_at"] < datetime.now():
            del self._tokens[token]
            return None
        return entry["username"]

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)


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
