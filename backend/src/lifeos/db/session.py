"""Transaction helpers enforcing the RLS model (design §24.1, tasks T1.1).

* `user_session(user_id)` — every user-facing request/tool call. Assumes the non-privileged
  `lifeos_app` role for the transaction and sets `app.user_id`, so PostgreSQL row-level
  security confines every statement to that user's rows.
* `system_session()` — worker jobs, auth lookups before a user is known, admin queries after a
  staff check. Runs as the schema owner (RLS not forced), so callers MUST filter by user_id.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.db.engine import get_sessionmaker

APP_ROLE = "lifeos_app"


@asynccontextmanager
async def user_session(
    user_id: UUID, *, sessionmaker: async_sessionmaker[AsyncSession] | None = None
) -> AsyncIterator[AsyncSession]:
    factory = sessionmaker or get_sessionmaker()
    async with factory() as session, session.begin():
        await session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
        await session.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)})
        yield session


@asynccontextmanager
async def system_session(
    *, sessionmaker: async_sessionmaker[AsyncSession] | None = None
) -> AsyncIterator[AsyncSession]:
    factory = sessionmaker or get_sessionmaker()
    async with factory() as session, session.begin():
        yield session
