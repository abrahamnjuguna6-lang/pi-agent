"""Integration-test fixtures: real PostgreSQL (pgvector) and Redis.

Locally, integration tests are skipped when the services are unreachable
(`docker compose -f infra/docker-compose.yml up -d postgres redis`). CI sets
REQUIRE_SERVICES=1 so an unreachable service fails the run instead of skipping.
"""

import os
import socket
from urllib.parse import urlparse

import pytest

from tests.conftest import TEST_ENV


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or 0), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.integration)
    services_up = _reachable(TEST_ENV["DATABASE_URL"]) and _reachable(TEST_ENV["REDIS_URL"])
    if services_up:
        return
    if os.environ.get("REQUIRE_SERVICES") == "1":
        raise pytest.UsageError("REQUIRE_SERVICES=1 but PostgreSQL/Redis are unreachable")
    skip = pytest.mark.skip(reason="PostgreSQL/Redis not reachable; start docker compose services")
    for item in items:
        item.add_marker(skip)


@pytest.fixture
def database_url() -> str:
    return TEST_ENV["DATABASE_URL"]


@pytest.fixture
def redis_url() -> str:
    return TEST_ENV["REDIS_URL"]
