"""Shared pytest configuration.

Test settings are supplied through environment variables so application code reads them the
same way it does in production (design §36.4).
"""

import os
from collections.abc import Iterator

import pytest

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


@pytest.fixture
def test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Apply the test environment and clear the cached settings around the test."""
    from lifeos.config import get_settings

    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield TEST_ENV
    get_settings.cache_clear()
