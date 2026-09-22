"""Integration-test fixtures: real PostgreSQL (pgvector) and Redis.

Locally, integration tests are skipped when the services are unreachable
(`docker compose -f infra/docker-compose.yml up -d postgres redis`). CI sets
REQUIRE_SERVICES=1 so an unreachable service fails the run instead of skipping.

The test database is migrated once per session (alembic upgrade head); every test that uses
the `db` fixture starts from empty application tables (TRUNCATE after each test).
"""

import os
import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from lifeos.db.engine import async_url, sync_url
from tests.conftest import TEST_ENV

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


@pytest.fixture(scope="session")
def database_url() -> str:
    return TEST_ENV["DATABASE_URL"]


@pytest.fixture(scope="session")
def redis_url() -> str:
    return TEST_ENV["REDIS_URL"]


def alembic_config(database_url: str) -> Config:
    cfg = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", "lifeos.db:migrations")
    cfg.set_main_option("sqlalchemy.url", sync_url(database_url))
    return cfg


@pytest.fixture(scope="session")
def migrated(database_url: str) -> str:
    """Bring the test database to head once per session."""
    command.upgrade(alembic_config(database_url), "head")
    return database_url


@pytest.fixture(scope="session")
async def engine(migrated: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(async_url(migrated), pool_size=5)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
async def app_tables(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename NOT LIKE 'checkpoint%' AND tablename <> 'alembic_version'"
            )
        )
        return [r[0] for r in rows]


@pytest.fixture
async def db(engine: AsyncEngine, app_tables: list[str]) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessionmaker bound to the migrated test DB; application tables are emptied after the test."""
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        # TRUNCATE does not fire the row-level append-only triggers.
        await conn.execute(text(f"TRUNCATE {', '.join(app_tables)} RESTART IDENTITY CASCADE"))


# API harness fixture (tests/integration/api_harness.py), shared by all integration modules.
from tests.integration.api_harness import api  # noqa: E402, F401
