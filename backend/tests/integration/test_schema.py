"""T1.2: schema migrations, CHECK/UNIQUE constraints, and ORM ↔ database drift."""

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable, Iterator
from decimal import Decimal

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.db import models as m
from lifeos.db.engine import sync_url
from lifeos.db.models import Base
from lifeos.db.session import system_session
from tests import factories as f
from tests.integration.conftest import alembic_config

Maker = async_sessionmaker[AsyncSession]


# --------------------------------------------------------------------------- migrations


@pytest.fixture
def scratch_db(database_url: str) -> Iterator[str]:
    """A throwaway database so up/down migrations never disturb the shared test DB."""
    name = f"lifeos_mig_{uuid.uuid4().hex[:8]}"
    admin = database_url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield database_url.rsplit("/", 1)[0] + "/" + name
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _public_tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows} - {"alembic_version"}


def test_migrate_empty_db_up_down_up(scratch_db: str) -> None:
    cfg = alembic_config(scratch_db)
    command.upgrade(cfg, "head")
    assert _public_tables(scratch_db) == set(Base.metadata.tables)
    command.downgrade(cfg, "base")
    assert _public_tables(scratch_db) == set()
    command.upgrade(cfg, "head")
    assert _public_tables(scratch_db) == set(Base.metadata.tables)


def test_models_match_database(migrated: str) -> None:
    """Drift guard: ORM columns/types/nullability/FKs/uniques equal the migrated schema.

    CHECK constraints and indexes are owned by the SQL migrations and verified behaviourally.
    """

    def include_object(
        obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
    ) -> bool:
        if type_ == "index":
            return False
        return not (
            type_ == "table"
            and name is not None
            and (name.startswith("checkpoint") or name == "alembic_version")
        )

    engine = create_engine(sync_url(migrated))
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "include_object": include_object})
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diff == [], f"ORM models drifted from the migrated schema: {diff}"


# --------------------------------------------------------------------------- constraints


async def expect_violation(db: Maker, build: Callable[[AsyncSession, m.User], Awaitable[None]]) -> None:
    async with system_session(sessionmaker=db) as s:
        user = await f.make_user(s)
    with pytest.raises(IntegrityError):
        async with system_session(sessionmaker=db) as s:
            await build(s, user)


async def expect_ok(db: Maker, build: Callable[[AsyncSession, m.User], Awaitable[None]]) -> None:
    async with system_session(sessionmaker=db) as s:
        user = await f.make_user(s)
        await build(s, user)


async def _goal(s: AsyncSession, user: m.User) -> m.Goal:
    goal = f.GoalFactory(user_id=user.id)
    await f.persist(s, goal)
    return goal


@pytest.mark.parametrize(
    ("direction", "target", "baseline"),
    [
        ("higher_is_better", Decimal("0"), None),  # R1.3: division by zero
        ("lower_is_better", Decimal("20"), None),  # baseline required
        ("lower_is_better", Decimal("20"), Decimal("20")),  # baseline == target
    ],
)
async def test_objective_range_rules(
    db: Maker, direction: str, target: Decimal, baseline: Decimal | None
) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        goal = await _goal(s, user)
        obj = f.ObjectiveFactory(
            user_id=user.id,
            goal_id=goal.id,
            metric_direction=direction,
            target_value=target,
            baseline_value=baseline,
        )
        await f.persist(s, obj)

    await expect_violation(db, build)


@pytest.mark.parametrize("note", [None, "", "   "])
async def test_skipped_checkin_requires_reason(db: Maker, note: str | None) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        action = await f.make_daily_action(s, user)
        await f.persist(
            s,
            m.CheckinRecord(
                daily_action_id=action.id,
                user_id=user.id,
                previous_status="Planned",
                new_status="Skipped",
                transition_source="User",
                note=note,
            ),
        )

    await expect_violation(db, build)


async def test_skipped_checkin_with_reason_ok(db: Maker) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        action = await f.make_daily_action(s, user)
        await f.persist(
            s,
            m.CheckinRecord(
                daily_action_id=action.id,
                user_id=user.id,
                previous_status="Planned",
                new_status="Skipped",
                transition_source="User",
                note="too tired",
            ),
        )

    await expect_ok(db, build)


@pytest.mark.parametrize(
    ("result", "percent", "note", "ok"),
    [
        ("partial", None, None, False),  # partial requires a percent
        ("partial", 100, None, False),  # partial is 1..99
        ("partial", 50, None, True),
        ("completed", None, None, True),
        ("completed", 50, None, False),  # a percent implies partial
        ("skipped", None, None, False),  # skip reason required
        ("skipped", None, "sick", True),
    ],
)
async def test_habit_occurrence_rules(
    db: Maker, result: str, percent: int | None, note: str | None, ok: bool
) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        tree = await f.make_goal_tree(s, user)
        await f.persist(
            s,
            m.HabitOccurrenceRecord(
                habit_id=tree["habit"].id,
                user_id=user.id,
                occurrence_date=dt.date(2026, 9, 21),
                result=result,
                completion_percent=percent,
                note=note,
            ),
        )

    await (expect_ok(db, build) if ok else expect_violation(db, build))


async def test_ai_inferred_memory_requires_inference_flag(db: Maker) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        await f.make_memory_entry(s, user, source="AI-inferred", is_inference=False, type="Pattern")

    await expect_violation(db, build)


@pytest.mark.parametrize(("source_type", "has_source_id"), [("MANUAL", True), ("ROUTINE_ENTRY", False)])
async def test_daily_action_source_id_rule(db: Maker, source_type: str, has_source_id: bool) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        await f.make_daily_action(
            s, user, source_type=source_type, source_id=uuid.uuid4() if has_source_id else None
        )

    await expect_violation(db, build)


# --------------------------------------------------------------------------- uniques


async def test_daily_action_occurrence_unique(db: Maker) -> None:
    source = uuid.uuid4()

    async def build(s: AsyncSession, user: m.User) -> None:
        await f.make_daily_action(s, user, source_type="HABIT", source_id=source)
        # Same occurrence rescheduled to another date still collides on occurrence_date.
        await f.make_daily_action(
            s,
            user,
            source_type="HABIT",
            source_id=source,
            date=dt.date(2026, 9, 22),
            occurrence_date=dt.date(2026, 9, 21),
        )

    await expect_violation(db, build)


async def test_manual_actions_not_unique_constrained(db: Maker) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        await f.make_daily_action(s, user)
        await f.make_daily_action(s, user)

    await expect_ok(db, build)


async def test_one_briefing_per_local_date(db: Maker) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        for _ in range(2):
            await f.persist(
                s,
                m.DailyBriefing(
                    user_id=user.id,
                    local_date=dt.date(2026, 9, 21),
                    content={},
                    narrative_status="template",
                    generated_at=dt.datetime(2026, 9, 21, 2, tzinfo=dt.UTC),
                ),
            )

    await expect_violation(db, build)


async def test_one_ceo_session_per_week(db: Maker) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        for _ in range(2):
            await f.persist(
                s,
                m.WeeklyCeoSession(
                    user_id=user.id,
                    week_start_date=dt.date(2026, 9, 14),
                    scheduled_at=dt.datetime(2026, 9, 20, 16, tzinfo=dt.UTC),
                    grace_ends_at=dt.datetime(2026, 9, 22, 21, tzinfo=dt.UTC),
                    status="scheduled",
                    pre_session_briefing={},
                ),
            )

    await expect_violation(db, build)


def _reflection(user: m.User, type_: str) -> m.Reflection:
    return m.Reflection(user_id=user.id, date=dt.date(2026, 9, 21), type=type_, content="…")


async def test_one_daily_reflection_per_day(db: Maker) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        await f.persist(s, _reflection(user, "daily"))
        await f.persist(s, _reflection(user, "daily"))

    await expect_violation(db, build)


async def test_multiple_accountability_reflections_per_day_allowed(db: Maker) -> None:
    async def build(s: AsyncSession, user: m.User) -> None:
        await f.persist(s, _reflection(user, "accountability"))
        await f.persist(s, _reflection(user, "accountability"))
        await f.persist(s, _reflection(user, "daily"))

    await expect_ok(db, build)
