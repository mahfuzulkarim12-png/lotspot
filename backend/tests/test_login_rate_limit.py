"""Login throttling (PCI DSS v4 Req 8.3.4): lock a key out after too many
consecutive failures, and clear the lockout on a success."""

from datetime import datetime, timedelta

from auth import LoginRateLimiter


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 1, 1, 12, 0, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def test_locks_out_after_max_attempts():
    limiter = LoginRateLimiter(max_attempts=3, lockout=timedelta(minutes=30))
    assert limiter.is_locked("user:admin") is False

    limiter.record_failure("user:admin")
    limiter.record_failure("user:admin")
    assert limiter.is_locked("user:admin") is False

    limiter.record_failure("user:admin")
    assert limiter.is_locked("user:admin") is True


def test_lockout_is_scoped_to_the_key():
    limiter = LoginRateLimiter(max_attempts=1, lockout=timedelta(minutes=30))
    limiter.record_failure("user:admin")
    assert limiter.is_locked("user:admin") is True
    assert limiter.is_locked("user:someone-else") is False
    assert limiter.is_locked("ip:127.0.0.1") is False


def test_success_resets_failure_count():
    limiter = LoginRateLimiter(max_attempts=3, lockout=timedelta(minutes=30))
    limiter.record_failure("user:admin")
    limiter.record_failure("user:admin")
    limiter.record_success("user:admin")

    # Only one more failure after a reset must not trip the lockout that two
    # pre-reset failures plus one new one would have.
    limiter.record_failure("user:admin")
    assert limiter.is_locked("user:admin") is False


def test_lockout_expires_after_the_configured_window():
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=1, lockout=timedelta(minutes=30), now=clock)
    limiter.record_failure("user:admin")
    assert limiter.is_locked("user:admin") is True

    clock.advance(minutes=29)
    assert limiter.is_locked("user:admin") is True

    clock.advance(minutes=2)
    assert limiter.is_locked("user:admin") is False
