"""T1.1 / T1.3: row-level security isolation, grants, and append-only history tables."""

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import CursorResult, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.db import models as m
from lifeos.db.session import system_session, user_session
from tests import factories as f

Maker = async_sessionmaker[AsyncSession]
SYSTEM_ONLY = {"background_job_runs", "developer_alerts", "account_deletion_requests"}


async def two_users_with_goals(db: Maker) -> tuple[m.User, m.User]:
    async with system_session(sessionmaker=db) as s:
        a, b = await f.make_user(s), await f.make_user(s)
        await f.make_goal_tree(s, a)
        await f.make_goal_tree(s, b)
    return a, b


# --------------------------------------------------------------------------- isolation


async def test_user_session_sees_only_own_rows(db: Maker) -> None:
    a, _b = await two_users_with_goals(db)
    async with user_session(a.id, sessionmaker=db) as s:
        goals = (await s.execute(select(m.Goal))).scalars().all()
        users = (await s.execute(select(m.User))).scalars().all()
    assert {g.user_id for g in goals} == {a.id}
    assert [u.id for u in users] == [a.id]


async def test_system_session_sees_all_rows(db: Maker) -> None:
    a, b = await two_users_with_goals(db)
    async with system_session(sessionmaker=db) as s:
        owners = set((await s.execute(select(m.Goal.user_id))).scalars())
    assert owners == {a.id, b.id}


async def test_cross_tenant_update_and_delete_affect_nothing(db: Maker) -> None:
    a, b = await two_users_with_goals(db)
    async with user_session(a.id, sessionmaker=db) as s:
        upd = await s.execute(update(m.Goal).where(m.Goal.user_id == b.id).values(title="hijacked"))
        dele = await s.execute(text("DELETE FROM goals WHERE user_id = :b"), {"b": b.id})
    assert cast(CursorResult[Any], upd).rowcount == 0
    assert cast(CursorResult[Any], dele).rowcount == 0
    async with system_session(sessionmaker=db) as s:
        titles = set((await s.execute(select(m.Goal.title).where(m.Goal.user_id == b.id))).scalars())
    assert "hijacked" not in titles


async def test_cannot_insert_rows_for_another_user(db: Maker) -> None:
    a, b = await two_users_with_goals(db)
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with user_session(a.id, sessionmaker=db) as s:
            await f.persist(s, f.GoalFactory(user_id=b.id))


async def test_app_role_without_user_id_sees_nothing(db: Maker) -> None:
    await two_users_with_goals(db)
    async with system_session(sessionmaker=db) as s:
        await s.execute(text("SET LOCAL ROLE lifeos_app"))
        count = (await s.execute(text("SELECT count(*) FROM goals"))).scalar_one()
    assert count == 0  # fails closed when app.user_id is unset


async def test_app_role_cannot_read_system_only_tables(db: Maker) -> None:
    for table in [*SYSTEM_ONLY, "agent_trace_payloads", "auth_refresh_token_history"]:
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with user_session(uuid.uuid4(), sessionmaker=db) as s:
                await s.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))  # noqa: S608 - fixed names


# --------------------------------------------------------------------------- meta-tests


async def test_every_user_scoped_table_has_rls(db: Maker) -> None:
    """Future tables cannot forget RLS: any table with user_id must be protected (or system-only)."""
    async with system_session(sessionmaker=db) as s:
        rows = await s.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                  AND EXISTS (SELECT 1 FROM information_schema.columns col
                              WHERE col.table_schema = 'public' AND col.table_name = c.relname
                                AND col.column_name = 'user_id')
                """
            )
        )
        unprotected = {name for name, rls in rows if not rls} - SYSTEM_ONLY
    assert unprotected == set()


async def test_every_table_granted_to_app_role_has_rls(db: Maker) -> None:
    async with system_session(sessionmaker=db) as s:
        rows = await s.execute(
            text(
                """
                SELECT DISTINCT g.table_name, c.relrowsecurity
                FROM information_schema.role_table_grants g
                JOIN pg_class c ON c.relname = g.table_name
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                WHERE g.grantee = 'lifeos_app' AND g.table_schema = 'public'
                """
            )
        )
        granted_without_rls = {name for name, rls in rows if not rls}
    assert granted_without_rls == set()


# --------------------------------------------------------------------------- append-only


async def _checkin(db: Maker) -> tuple[m.User, uuid.UUID]:
    async with system_session(sessionmaker=db) as s:
        user = await f.make_user(s)
        action = await f.make_daily_action(s, user)
        checkin_id = (
            await s.execute(select(m.CheckinRecord.id).where(m.CheckinRecord.daily_action_id == action.id))
        ).scalar_one()
    return user, checkin_id


async def test_checkin_update_rejected(db: Maker) -> None:
    _, checkin_id = await _checkin(db)
    with pytest.raises(DBAPIError, match="append-only"):
        async with system_session(sessionmaker=db) as s:
            await s.execute(
                text("UPDATE checkin_records SET note = 'edited' WHERE id = :i"), {"i": checkin_id}
            )


async def test_checkin_delete_rejected(db: Maker) -> None:
    _, checkin_id = await _checkin(db)
    with pytest.raises(DBAPIError, match="append-only"):
        async with system_session(sessionmaker=db) as s:
            await s.execute(text("DELETE FROM checkin_records WHERE id = :i"), {"i": checkin_id})


async def test_checkin_update_rejected_even_with_purge_flag(db: Maker) -> None:
    _, checkin_id = await _checkin(db)
    with pytest.raises(DBAPIError, match="append-only"):
        async with system_session(sessionmaker=db) as s:
            await s.execute(text("SELECT set_config('app.allow_history_delete', 'on', true)"))
            await s.execute(text("UPDATE checkin_records SET note = 'x' WHERE id = :i"), {"i": checkin_id})


async def test_purge_transaction_can_delete_user_with_history(db: Maker) -> None:
    user, checkin_id = await _checkin(db)
    async with system_session(sessionmaker=db) as s:
        await s.execute(text("SELECT set_config('app.allow_history_delete', 'on', true)"))
        await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
    async with system_session(sessionmaker=db) as s:
        remaining = (
            await s.execute(text("SELECT count(*) FROM checkin_records WHERE id = :i"), {"i": checkin_id})
        ).scalar_one()
    assert remaining == 0


async def test_user_delete_without_purge_flag_blocked_by_history(db: Maker) -> None:
    user, _ = await _checkin(db)
    with pytest.raises(DBAPIError, match="append-only"):
        async with system_session(sessionmaker=db) as s:
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
