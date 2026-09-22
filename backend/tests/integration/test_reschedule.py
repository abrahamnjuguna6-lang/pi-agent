"""T5.6 / T5.8: source-aware reschedule, cancel, history, matching, Completion Rate on the destination day."""

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session, user_session
from lifeos.domain.analytics.completion import completion_rate
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import actions, generate, goal_with_habit, user_id_of

MON, TUE = date(2026, 9, 21), date(2026, 9, 22)


async def routine_action(api: Api, me: AuthedClient) -> m.DailyAction:
    t = (await me.post("/routine-templates", {"title": "Weekdays", "active_days": [1, 2, 3, 4, 5]})).json()[
        "data"
    ]
    await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "Learning session", "start_time": "16:00:00", "end_time": "17:00:00"},
    )
    await generate(api, me, MON)
    (action,) = await actions(api, date=MON)
    return action


async def test_routine_reschedule_creates_exception_and_keeps_template(api: Api) -> None:
    me = await api.as_user("quinn@example.com")
    action = await routine_action(api, me)
    r = await me.patch(
        f"/daily-actions/{action.id}/schedule",
        {"date": "2026-09-21", "start_time": "18:00:00", "end_time": "19:00:00", "reason": "late meeting"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["scheduled_start"] == "2026-09-21T18:00:00+00:00"
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        ex = (await s.execute(select(m.RoutineException))).scalar_one()
        entry = (await s.execute(select(m.RoutineEntry))).scalar_one()
    assert (ex.exception_type, ex.date, ex.daily_action_id) == ("modified", MON, action.id)
    assert entry.start_time == time(16, 0)  # Template unchanged (R2.5)

    history = (await me.get(f"/daily-actions/{action.id}/schedule-history")).json()["data"]
    assert [(h["change_type"], h["previous_start"], h["new_start"], h["reason"]) for h in history] == [
        ("rescheduled", "2026-09-21T16:00:00+00:00", "2026-09-21T18:00:00+00:00", "late meeting")
    ]  # original planned time is preserved (R2.8)


async def test_habit_reschedule_creates_override(api: Api) -> None:
    me = await api.as_user("quinn@example.com")
    await goal_with_habit(me, preferred_start="18:00:00")
    await generate(api, me, MON)
    (action,) = await actions(api)
    await me.patch(
        f"/daily-actions/{action.id}/schedule",
        {"date": "2026-09-21", "start_time": "20:00:00", "end_time": "20:30:00"},
    )
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        override = (await s.execute(select(m.HabitOccurrenceOverride))).scalar_one()
        stored = (await s.execute(select(m.Habit))).scalar_one()
    assert (override.override_type, override.occurrence_date) == ("rescheduled", MON)
    assert stored.preferred_start == time(18, 0)  # recurrence/definition unchanged


async def test_cross_day_move_counts_only_on_destination(api: Api) -> None:
    me = await api.as_user("quinn@example.com")
    action = await routine_action(api, me)
    await me.patch(
        f"/daily-actions/{action.id}/schedule",
        {"date": "2026-09-22", "start_time": "09:00:00", "end_time": "10:00:00"},
    )
    await generate(api, me, TUE)  # Tuesday's own occurrence must not collide with the moved Monday one
    rows = await actions(api, date=TUE)
    assert sorted(r.occurrence_date for r in rows) == [MON, TUE]
    assert (await actions(api, date=MON)) == []

    uid = await user_id_of(api, me)
    await me.post(f"/daily-actions/{action.id}/checkins", {"new_status": "Completed"})
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        monday = await completion_rate(s, uid, MON, MON, datetime(2026, 9, 23, tzinfo=UTC))
        tuesday = await completion_rate(s, uid, TUE, TUE, datetime(2026, 9, 23, tzinfo=UTC))
    assert monday.rate is None  # nothing scheduled on Monday any more
    assert (tuesday.completed, tuesday.planned, str(tuesday.rate)) == (1, 1, "0.5000")


async def test_started_action_cannot_be_rescheduled(api: Api) -> None:
    me = await api.as_user("quinn@example.com")
    action = await routine_action(api, me)
    await me.post(f"/daily-actions/{action.id}/checkins", {"new_status": "Started"})
    r = await me.patch(
        f"/daily-actions/{action.id}/schedule",
        {"date": "2026-09-21", "start_time": "18:00:00", "end_time": "19:00:00"},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "INVALID_TRANSITION")


async def test_user_cancel_is_not_regenerated_and_excluded_from_rate(api: Api) -> None:
    me = await api.as_user("quinn@example.com")
    action = await routine_action(api, me)
    r = await me.post(f"/daily-actions/{action.id}/cancel", {"reason": "public holiday"})
    assert r.json()["data"]["lifecycle_state"] == "cancelled"
    assert await generate(api, me, MON) == 0  # the `removed` exception prevents regeneration
    r = await me.post(f"/daily-actions/{action.id}/checkins", {"new_status": "Completed"})
    assert r.status_code == 409
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        assert (await completion_rate(s, uid, MON, MON, api.clock.now())).rate is None
    assert (await me.get("/daily-actions", date="2026-09-21")).json()["data"] == []
    cancelled = (await me.get("/daily-actions", date="2026-09-21", include_cancelled="true")).json()["data"]
    assert [a["lifecycle_state"] for a in cancelled] == ["cancelled"]


async def test_natural_language_matching(api: Api) -> None:  # R2.10
    me = await api.as_user("quinn@example.com")
    await routine_action(api, me)
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        hit = await api.container.reschedule.match_actions(s, uid, MON, time(16, 0), "learning")
        miss = await api.container.reschedule.match_actions(s, uid, MON, time(14, 0), "learning")
    assert [x.action.title for x in hit] == ["Learning session"]
    assert miss == []


async def test_other_user_cannot_reschedule(api: Api) -> None:
    owner = await api.as_user("quinn@example.com")
    action = await routine_action(api, owner)
    intruder = await api.as_user("ray@example.com")
    r = await intruder.patch(
        f"/daily-actions/{action.id}/schedule",
        {"date": "2026-09-21", "start_time": "18:00:00", "end_time": "19:00:00"},
    )
    assert r.status_code == 404
    assert (await intruder.get(f"/daily-actions/{uuid.uuid4()}/schedule-history")).status_code == 404
