"""T1.4: factories build a complete, valid user graph."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests import factories as f


async def test_factories_build_complete_user_graph(db: async_sessionmaker[AsyncSession]) -> None:
    async with system_session(sessionmaker=db) as s:
        user = await f.make_user(s)
        tree = await f.make_goal_tree(s, user)
        routine = await f.make_routine(s, user, entries=3)
        a1 = await f.make_daily_action(s, user, source_type="TASK", source_id=tree["task"].id)
        a2 = await f.make_daily_action(s, user)
        commitment = await f.make_commitment(s, user, [a1, a2], completion_condition="all")
        await f.make_memory_entry(s, user)

    async with system_session(sessionmaker=db) as s:

        async def count(model: type) -> int:
            return (await s.execute(select(func.count()).select_from(model))).scalar_one()

        assert await count(m.Goal) == 1
        assert await count(m.Habit) == 1
        assert await count(m.RoutineEntry) == len(routine["entries"]) == 3
        assert await count(m.DailyAction) == 2
        assert await count(m.CheckinRecord) == 2  # initial none → Planned per action
        assert await count(m.CommitmentLink) == 2
        assert commitment.completion_condition == "all"
        assert await count(m.MemoryStoreEntry) == 1
