"""T5.3: Daily Action generation — idempotency, initial check-in, DST, midnight, pauses, routine habits."""

from datetime import date, timedelta

from sqlalchemy import func, select

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests.integration.api_harness import Api
from tests.integration.sched_helpers import actions, generate, goal_with_habit

MON = date(2026, 9, 21)  # harness clock: 2026-09-21 12:00 UTC (a Monday)


async def test_generation_is_idempotent_with_initial_checkins(api: Api) -> None:
    me = await api.as_user("lee@example.com")
    t = (await me.post("/routine-templates", {"title": "Weekdays", "active_days": [1, 2, 3, 4, 5]})).json()[
        "data"
    ]
    await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "Deep work", "start_time": "09:00:00", "end_time": "10:00:00"},
    )
    await goal_with_habit(me)

    assert await generate(api, me) == 4  # today + tomorrow × (routine entry + habit)
    assert await generate(api, me) == 0  # second run creates nothing
    rows = await actions(api)
    assert len(rows) == 4
    assert {r.status for r in rows} == {"Planned"}
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        checkins = list((await s.execute(select(m.CheckinRecord))).scalars())
        instances = (await s.execute(select(func.count()).select_from(m.RoutineInstance))).scalar_one()
    assert len(checkins) == 4
    assert all(
        (c.previous_status, c.new_status, c.transition_source) == (None, "Planned", "System")
        for c in checkins
    )
    assert instances == 2  # one Routine Instance per template per day (R2.3)
    routine = [r for r in rows if r.source_type == "ROUTINE_ENTRY"]
    assert all(r.routine_instance_id is not None for r in routine)


async def test_day_view_via_api(api: Api) -> None:
    me = await api.as_user("lee@example.com")
    await goal_with_habit(me, preferred_start="18:00:00")
    t = (await me.post("/routine-templates", {"title": "Weekdays", "active_days": [1]})).json()["data"]
    await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "Standup", "start_time": "09:00:00", "end_time": "09:15:00"},
    )
    await generate(api, me, MON)
    body = (await me.get("/daily-actions")).json()
    assert body["meta"]["date"] == "2026-09-21"
    assert [a["title"] for a in body["data"]] == ["Standup", "Run"]  # ordered by start (R3.8)


async def test_dst_day_and_midnight_crossing(api: Api) -> None:
    me = await api.as_user("lee@example.com")
    await me.patch("/me", {"timezone": "America/New_York"})
    t = (await me.post("/routine-templates", {"title": "Sundays", "active_days": [0]})).json()["data"]
    await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "Early", "start_time": "02:30:00", "end_time": "03:30:00"},
    )
    await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "Late", "start_time": "23:30:00", "end_time": "00:30:00"},
    )
    await generate(api, me, date(2026, 3, 8))  # spring-forward Sunday
    early, late = await actions(api)
    assert early.scheduled_start.isoformat() == "2026-03-08T07:00:00+00:00"  # 02:30 → 03:00 EDT
    assert late.scheduled_end - late.scheduled_start == timedelta(hours=1)
    assert late.scheduled_end.isoformat() == "2026-03-09T04:30:00+00:00"  # ends next local day


async def test_routine_linked_habit_not_duplicated(api: Api) -> None:
    me = await api.as_user("lee@example.com")
    _, habit = await goal_with_habit(me)
    t = (await me.post("/routine-templates", {"title": "Mondays", "active_days": [1]})).json()["data"]
    await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "Run (routine)", "start_time": "06:00:00", "end_time": "06:30:00", "habit_id": habit["id"]},
    )
    await generate(api, me, MON, MON + timedelta(days=1))
    rows = await actions(api)
    assert [(r.date, r.source_type) for r in rows] == [
        (MON, "ROUTINE_ENTRY"),
        (MON + timedelta(days=1), "HABIT"),
    ]


async def test_pause_cancels_future_and_resume_regenerates(api: Api) -> None:
    me = await api.as_user("lee@example.com")
    _, habit = await goal_with_habit(me, preferred_start="18:00:00")
    await generate(api, me)  # today 18:00 and tomorrow 18:00
    assert len(await actions(api, lifecycle_state="active")) == 2

    r = await me.post(f"/habits/{habit['id']}/pause", {})
    assert r.status_code == 200
    assert (await actions(api, lifecycle_state="active")) == []  # future Planned occurrences cancelled
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        history = list((await s.execute(select(m.DailyActionScheduleHistory))).scalars())
    assert {(h.change_type, h.changed_by) for h in history} == {("habit_paused", "System")}
    assert await generate(api, me) == 0  # paused days are not generated
    assert (await me.get(f"/habits/{habit['id']}")).json()["data"]["paused"] is True

    api.clock.set(api.clock.now() + timedelta(days=1))  # next day (the 1 h access token has expired)
    me = await api.relogin("lee@example.com")
    assert (await me.post(f"/habits/{habit['id']}/resume")).status_code == 200
    assert await generate(api, me) == 2  # fresh occurrences (index only constrains ACTIVE rows)
    assert (await me.post(f"/habits/{habit['id']}/resume")).status_code == 409


async def test_archived_goal_habits_are_not_generated(api: Api) -> None:
    me = await api.as_user("lee@example.com")
    goal_id, _ = await goal_with_habit(me)
    await me.post(f"/goals/{goal_id}/archive")
    assert await generate(api, me) == 0
