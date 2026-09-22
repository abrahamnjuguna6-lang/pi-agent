"""T5.7: timezone change re-anchors future Planned actions; history untouched (design §12.5, R21.4)."""

from datetime import date

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session
from lifeos.domain.timeutil import to_local
from tests.integration.api_harness import Api
from tests.integration.sched_helpers import actions, generate

MON = date(2026, 9, 21)  # clock: Mon 12:00 UTC


async def test_future_actions_keep_wall_clock_past_untouched(api: Api) -> None:
    me = await api.as_user("vic@example.com")
    t = (await me.post("/routine-templates", {"title": "Weekdays", "active_days": [1, 2, 3, 4, 5]})).json()[
        "data"
    ]
    await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "Morning", "start_time": "08:00:00", "end_time": "09:00:00"},
    )
    await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "Evening", "start_time": "18:00:00", "end_time": "19:00:00"},
    )
    task = (await me.post("/tasks", {"title": "Dentist", "duration_minutes": 60})).json()["data"]
    await me.post(f"/tasks/{task['id']}/schedule", {"date": "2026-09-22", "start_time": "09:00:00"})
    await generate(api, me)  # Mon + Tue
    before = {(a.title, a.date): a for a in await actions(api)}
    past = before[("Morning", MON)]  # 08:00 UTC Monday is already in the past at 12:00
    past_start = past.scheduled_start

    r = await me.patch("/me", {"timezone": "Africa/Nairobi"})  # UTC → UTC+3
    assert r.status_code == 200

    after = {(a.title, a.date): a for a in await actions(api)}
    assert after[("Morning", MON)].scheduled_start == past_start  # past rows unchanged
    for key in [("Evening", MON), ("Morning", date(2026, 9, 22)), ("Dentist", date(2026, 9, 22))]:
        local = to_local(after[key].scheduled_start, "Africa/Nairobi")
        original_local = to_local(before[key].scheduled_start, "UTC")
        assert (local.date(), local.time()) == (original_local.date(), original_local.time()), key
    assert (
        to_local(after[("Dentist", date(2026, 9, 22))].scheduled_start, "Africa/Nairobi").hour == 9
    )  # task keeps 09:00

    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        history = list((await s.execute(select(m.DailyActionScheduleHistory))).scalars())
        checkins = list((await s.execute(select(m.CheckinRecord))).scalars())
    assert len(history) == 4
    assert {(h.change_type, h.changed_by, h.reason) for h in history} == {
        ("timezone_change", "System", "UTC → Africa/Nairobi")
    }
    assert all(c.transition_source == "System" for c in checkins)  # check-in history untouched


async def test_started_actions_are_not_moved(api: Api) -> None:
    me = await api.as_user("vic@example.com")
    r = await me.post(
        "/daily-actions",
        {"title": "Call", "date": "2026-09-21", "start_time": "13:00:00", "end_time": "14:00:00"},
    )
    action_id = r.json()["data"]["id"]
    await me.post(f"/daily-actions/{action_id}/checkins", {"new_status": "Started"})
    await me.patch("/me", {"timezone": "America/New_York"})
    (row,) = await actions(api)
    assert row.scheduled_start.isoformat() == "2026-09-21T13:00:00+00:00"
