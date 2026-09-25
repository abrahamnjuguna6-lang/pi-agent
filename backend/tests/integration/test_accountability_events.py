"""T6.4: escalation driven by real check-ins — levels, episodes, flags, pauses (R8.1–8.8)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session, user_session
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import actions, generate, goal_with_habit, user_id_of

EMAIL = "kira@example.com"
MON = date(2026, 9, 21)  # the frozen clock is 2026-09-21 12:00 UTC


def days(*offsets: int) -> list[date]:
    return [MON - timedelta(days=o) for o in offsets]


async def habit_on(api: Api, me: AuthedClient, *dates: date, **overrides: Any) -> str:
    """A habit that started well before the window, with Daily Actions generated for `dates`."""
    _, habit = await goal_with_habit(me, start_date="2026-09-01", **overrides)
    await generate(api, me, *dates)
    return str(habit["id"])


async def check_in(me: AuthedClient, action: m.DailyAction, status: str, note: str | None = None) -> None:
    body = {"new_status": status} | ({"note": note} if note else {})
    r = await me.post(f"/daily-actions/{action.id}/checkins", body)
    assert r.status_code == 201, r.text


async def skip_days(api: Api, me: AuthedClient, *dates: date) -> None:
    for day in dates:
        for action in await actions(api, date=day, source_type="HABIT"):
            await check_in(me, action, "Skipped", f"skipped on {day}")


async def escalations(me: AuthedClient, **params: Any) -> list[dict[str, Any]]:
    r = await me.get("/accountability/escalations", **params)
    assert r.status_code == 200, r.text
    return list(r.json()["data"])


async def flags_of(api: Api, uid: Any) -> list[m.ProactiveFlag]:
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        return list(
            (
                await s.execute(
                    select(m.ProactiveFlag)
                    .where(
                        m.ProactiveFlag.user_id == uid,
                        m.ProactiveFlag.flag_type == "escalation_level_3plus",
                    )
                    .order_by(m.ProactiveFlag.created_at, m.ProactiveFlag.dedupe_key)
                )
            ).scalars()
        )


# --------------------------------------------------------------------------- source levels


async def test_third_skip_escalates_in_the_same_request(api: Api) -> None:  # R8.4, design §14.2
    me = await api.as_user(EMAIL)
    habit = await habit_on(api, me, *days(2, 1, 0))
    await skip_days(api, me, *days(2, 1))
    assert await escalations(me) == []  # two skips are not a pattern

    await skip_days(api, me, *days(0))
    (state,) = await escalations(me)
    assert (state["level"], state["source_type"], state["source_id"]) == (3, "HABIT", habit)
    assert (state["reflection_required"], state["source_title"]) == (False, "Run")
    assert [s["date"] for s in state["skips"]] == ["2026-09-21", "2026-09-20", "2026-09-19"]

    uid = await user_id_of(api, me)
    flags = await flags_of(api, uid)
    assert [(f.flag_type, f.dedupe_key) for f in flags] == [
        ("escalation_level_3plus", f"escalation:{state['escalation_episode_id']}:3")
    ]


async def test_five_skips_require_a_reflection(api: Api) -> None:  # R8.5
    me = await api.as_user(EMAIL)
    await habit_on(api, me, *days(5, 4, 3, 2, 1, 0))
    await skip_days(api, me, *days(5, 4))
    for action in await actions(api, date=days(3)[0], source_type="HABIT"):
        await check_in(me, action, "Completed")  # breaks the run, so this stays a Level 4 pattern
    await skip_days(api, me, *days(2, 1))
    assert (await escalations(me))[0]["level"] == 3

    await skip_days(api, me, *days(0))  # the fifth skip inside the 7-day window
    (state,) = await escalations(me)
    assert (state["level"], state["reflection_required"]) == (4, True)
    assert state["reflection_completed_at"] is None


async def test_sparse_habit_reaches_level_5_on_consecutive_occurrences(api: Api) -> None:  # R8.6
    me = await api.as_user(EMAIL)
    mwf = [date(2026, 9, d) for d in (9, 11, 14, 16, 18, 21)]  # Wed/Fri/Mon/Wed/Fri/Mon
    await habit_on(api, me, *mwf, recurrence_type="custom", recurrence_days=[1, 3, 5])
    generated = await actions(api, source_type="HABIT")
    assert [a.date for a in generated] == mwf
    await skip_days(api, me, *mwf[1:])  # five consecutive scheduled occurrences
    (state,) = await escalations(me)
    assert state["level"] == 5  # only three of them fall inside the 7-day window


async def test_skips_inside_a_pause_are_not_a_pattern(api: Api) -> None:  # R1.12, design §14.2
    me = await api.as_user(EMAIL)
    habit = await habit_on(api, me, *days(4, 3, 2, 1, 0))
    uid = await user_id_of(api, me)
    # a pause that covered the first three days (a pause is opened for today or later, so it is
    # inserted here the way it would have existed while it was running)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        s.add(
            m.HabitPausePeriod(
                habit_id=uuid.UUID(habit),
                user_id=uid,
                starts_on=days(4)[0],
                ends_on=days(2)[0],
                reason="travelling",
            )
        )
    await skip_days(api, me, *days(4, 3, 2, 1, 0))
    assert await escalations(me) == []  # only the two days outside the pause count

    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        assert await api.container.accountability.evaluate_user(s, uid) == []


async def test_routine_entries_escalate_too(api: Api) -> None:
    me = await api.as_user(EMAIL)
    template = (
        await me.post("/routine-templates", {"title": "Weekdays", "active_days": [0, 1, 2, 3, 4, 5, 6]})
    ).json()["data"]
    entry = (
        await me.post(
            f"/routine-templates/{template['id']}/entries",
            {"title": "Morning pages", "start_time": "06:00:00", "end_time": "06:30:00"},
        )
    ).json()["data"]
    await generate(api, me, *days(2, 1, 0))
    for day in days(2, 1, 0):
        for action in await actions(api, date=day, source_type="ROUTINE_ENTRY"):
            await check_in(me, action, "Skipped", "overslept")
    (state,) = await escalations(me)
    assert (state["level"], state["source_type"], state["source_id"]) == (3, "ROUTINE_ENTRY", entry["id"])
    assert state["source_title"] == "Morning pages"


async def test_completions_never_escalate(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await habit_on(api, me, *days(2, 1, 0))
    for day in days(2, 1, 0):
        for action in await actions(api, date=day, source_type="HABIT"):
            await check_in(me, action, "Completed")
    assert await escalations(me) == []


async def test_evaluation_is_idempotent(api: Api) -> None:  # design §14.2
    me = await api.as_user(EMAIL)
    await habit_on(api, me, *days(2, 1, 0))
    await skip_days(api, me, *days(2, 1, 0))
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        first = await api.container.accountability.evaluate_user(s, uid)
        assert [x.level for x in first] == [3]
        updated_at = first[0].updated_at
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        again = await api.container.accountability.evaluate_user(s, uid)
    assert (again[0].level, again[0].updated_at) == (3, updated_at)  # no new episode, no new event
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        events = list(
            (
                await s.execute(
                    select(m.DomainEvent).where(
                        m.DomainEvent.user_id == uid, m.DomainEvent.event_type == "escalation.level_changed"
                    )
                )
            ).scalars()
        )
    assert [(e.payload["previous_level"], e.payload["level"]) for e in events] == [(1, 3)]
    assert len(await flags_of(api, uid)) == 1


async def test_escalation_of_another_user_is_not_visible(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await habit_on(api, me, *days(2, 1, 0))
    await skip_days(api, me, *days(2, 1, 0))
    (state,) = await escalations(me)
    other = await api.as_user("mallory@example.com")
    assert await escalations(other) == []
    assert (await other.get(f"/accountability/escalations/{state['id']}")).status_code == 404


# --------------------------------------------------------------------------- occurrence levels 1/2


async def test_occurrence_triggers(api: Api) -> None:  # R8.2–8.3
    me = await api.as_user(EMAIL)
    await habit_on(api, me, MON, preferred_start="13:00:00", duration_minutes=30)  # 13:00–13:30, now 12:00
    uid = await user_id_of(api, me)
    (action,) = await actions(api, date=MON, source_type="HABIT")

    async def triggers() -> list[tuple[Any, int]]:
        async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
            due = await api.container.accountability.occurrence_triggers(s, uid)
        return [(t.daily_action_id, t.level) for t in due]

    assert await triggers() == []  # not started yet
    api.clock.set(datetime(2026, 9, 21, 13, 10, tzinfo=UTC))
    me = await api.relogin(EMAIL)  # the access token only lasts an hour
    assert await triggers() == [(action.id, 1)]  # after the start, still Planned → Reminder
    await check_in(me, action, "Started")
    assert await triggers() == []  # started inside its block: nothing due
    api.clock.set(datetime(2026, 9, 21, 13, 40, tzinfo=UTC))
    assert await triggers() == [(action.id, 2)]  # past its end → Nudge
    await check_in(me, action, "Completed")
    assert await triggers() == []
