"""Shared pytest configuration.

Test settings are supplied through environment variables so application code reads them the
same way it does in production (design §36.4).
"""

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from lifeos.domain.clock import FrozenClock

TEST_ENV = {
    "ENVIRONMENT": "test",
    "DATABASE_URL": os.environ.get(
        "TEST_DATABASE_URL", "postgresql://lifeos:lifeos@localhost:55432/lifeos_test"
    ),
    "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15"),
    "JWT_PRIVATE_KEY": "test-private-key",
    "JWT_PUBLIC_KEY": "test-public-key",
    "HMAC_KEY": "test-hmac-key",
    "ENCRYPTION_KEY": "test-encryption-key",
}


USER_TIMEZONES = ["UTC", "America/New_York", "Africa/Nairobi", "Australia/Lord_Howe"]


@pytest.fixture
def frozen_clock() -> "FrozenClock":
    from datetime import UTC, datetime

    from lifeos.domain.clock import FrozenClock

    return FrozenClock(datetime(2026, 9, 21, 12, 0, tzinfo=UTC))


@pytest.fixture(params=USER_TIMEZONES)
def user_tz(request: pytest.FixtureRequest) -> str:
    """Run a test once per representative timezone (incl. 30-minute DST: Lord Howe)."""
    return request.param


@pytest.fixture
def test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Apply the test environment and clear the cached settings around the test."""
    from lifeos.config import get_settings

    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield TEST_ENV
    get_settings.cache_clear()
