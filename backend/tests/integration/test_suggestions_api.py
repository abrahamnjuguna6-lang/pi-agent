"""T5.10: late-completion suggestions — nothing changes until the User accepts (R2.9, §19.9)."""

from datetime import timedelta

from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import actions


async def day_plan(api: Api, me: AuthedClient) -> list[str]:
    """Mon: Focus 10–11, Gym 12:30–13:30, Read 14–15 (UTC). Returns ids in order."""
    ids = []
    for title, start, end in (
        ("Focus", "10:00:00", "11:00:00"),
        ("Gym", "12:30:00", "13:30:00"),
        ("Read", "14:00:00", "15:00:00"),
    ):
        r = await me.post(
            "/daily-actions", {"title": title, "date": "2026-09-21", "start_time": start, "end_time": end}
        )
        ids.append(r.json()["data"]["id"])
    await me.patch("/me", {"sleep_time": "22:30:00", "wake_time": "06:00:00"})
    return ids


async def finish_late(api: Api, me: AuthedClient, action_id: str) -> None:
    # Clock is 12:00; Focus ended at 11:00 → completing now is a 60-minute overrun.
    await me.post(f"/daily-actions/{action_id}/checkins", {"new_status": "Completed"})
    await api.container.outbox.drain()


async def test_late_completion_proposes_shift_without_changing_anything(api: Api) -> None:
    me = await api.as_user("wes@example.com")
    focus, gym, read = await day_plan(api, me)
    before = {a.title: a.scheduled_start for a in await actions(api)}
    await finish_late(api, me, focus)

    (suggestion,) = (await me.get("/schedule-suggestions", status="proposed")).json()["data"]
    assert [p["daily_action_id"] for p in suggestion["proposal"]] == [gym, read]
    assert suggestion["proposal"][0]["new_start"] == "2026-09-21T13:30:00+00:00"  # +60 min, duration kept
    assert suggestion["proposal"][0]["new_end"] == "2026-09-21T14:30:00+00:00"
    assert {a.title: a.scheduled_start for a in await actions(api)} == before  # R2.9: no change yet

    r = await me.post(f"/schedule-suggestions/{suggestion['id']}/accept")
    assert r.json()["data"]["status"] == "accepted"
    after = {a.title: a.scheduled_start for a in await actions(api)}
    assert after["Gym"] == before["Gym"] + timedelta(hours=1)
    assert after["Read"] == before["Read"] + timedelta(hours=1)
    history = (await me.get(f"/daily-actions/{gym}/schedule-history")).json()["data"]
    assert (history[0]["changed_by"], history[0]["reason"]) == ("User", "late_completion_suggestion")
    assert (await me.post(f"/schedule-suggestions/{suggestion['id']}/accept")).status_code == 409


async def test_reject_changes_nothing(api: Api) -> None:
    me = await api.as_user("wes@example.com")
    focus, *_ = await day_plan(api, me)
    before = {a.title: a.scheduled_start for a in await actions(api)}
    await finish_late(api, me, focus)
    (suggestion,) = (await me.get("/schedule-suggestions")).json()["data"]
    assert (await me.post(f"/schedule-suggestions/{suggestion['id']}/reject")).json()["data"][
        "status"
    ] == "rejected"
    assert {a.title: a.scheduled_start for a in await actions(api)} == before


async def test_on_time_completion_creates_no_suggestion(api: Api) -> None:
    me = await api.as_user("wes@example.com")
    _, gym, _ = await day_plan(api, me)  # Gym ends 13:30, now is 12:00 → early
    await me.post(f"/daily-actions/{gym}/checkins", {"new_status": "Completed"})
    await api.container.outbox.drain()
    assert (await me.get("/schedule-suggestions")).json()["data"] == []


async def test_actions_changed_by_user_are_left_alone_on_accept(api: Api) -> None:
    me = await api.as_user("wes@example.com")
    focus, gym, _read = await day_plan(api, me)
    await finish_late(api, me, focus)
    await me.post(f"/daily-actions/{gym}/checkins", {"new_status": "Skipped", "note": "sore"})
    (suggestion,) = (await me.get("/schedule-suggestions")).json()["data"]
    before_gym = next(a for a in await actions(api) if a.title == "Gym").scheduled_start
    await me.post(f"/schedule-suggestions/{suggestion['id']}/accept")
    rows = {a.title: a for a in await actions(api)}
    assert rows["Gym"].scheduled_start == before_gym  # skipped action untouched
    assert rows["Read"].scheduled_start.hour == 15


async def test_expired_suggestion_cannot_be_accepted(api: Api) -> None:
    me = await api.as_user("wes@example.com")
    focus, *_ = await day_plan(api, me)
    await finish_late(api, me, focus)
    (suggestion,) = (await me.get("/schedule-suggestions")).json()["data"]
    api.clock.set(api.clock.now() + timedelta(days=1))
    me = await api.relogin("wes@example.com")
    r = await me.post(f"/schedule-suggestions/{suggestion['id']}/accept")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "INVALID_TRANSITION")
