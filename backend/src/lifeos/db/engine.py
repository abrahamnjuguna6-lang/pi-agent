"""Async SQLAlchemy engine (psycopg 3) and URL helpers."""

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from lifeos.config import get_settings


def _with_driver(url: str) -> str:
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    raise ValueError("DATABASE_URL must be a postgresql:// URL")


def sync_url(url: str) -> str:
    """SQLAlchemy URL for synchronous use (Alembic). psycopg 3 serves both sync and async."""
    return _with_driver(url)


def async_url(url: str) -> str:
    return _with_driver(url)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        async_url(str(settings.database_url)),
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(get_engine(), expire_on_commit=False)
