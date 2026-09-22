"""T5.5: scheduling Tasks and Task ↔ Daily Action sync (R1.9, R3.7, R3.10)."""

from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import actions


async def new_task(me: AuthedClient, **extra: object) -> str:
    r = await me.post("/tasks", {"title": "Write proposal", "duration_minutes": 45, **extra})
    return r.json()["data"]["id"]


async def test_scheduling_a_task_creates_task_action(api: Api) -> None:
    me = await api.as_user("pat@example.com")
    task = await new_task(me)
    r = await me.post(f"/tasks/{task}/schedule", {"date": "2026-09-22", "start_time": "10:00:00"})
    assert r.status_code == 201, r.text
    action = r.json()["data"]
    assert (action["source_type"], action["source_id"], action["date"]) == ("TASK", task, "2026-09-22")
    assert action["scheduled_start"] == "2026-09-22T10:00:00+00:00"
    assert action["scheduled_end"] == "2026-09-22T10:45:00+00:00"  # duration_minutes
    t = (await me.get(f"/tasks/{task}")).json()["data"]
    assert (t["scheduled_date"], t["scheduled_time"]) == ("2026-09-22", "10:00:00")
    history = (await me.get(f"/daily-actions/{action['id']}/checkins")).json()["data"]
    assert [h["new_status"] for h in history] == ["Planned"]

    again = await me.post(f"/tasks/{task}/schedule", {"date": "2026-09-23", "start_time": "10:00:00"})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "INVALID_TRANSITION")


async def test_action_status_drives_task_status(api: Api) -> None:
    me = await api.as_user("pat@example.com")
    task = await new_task(me)
    action = (
        await me.post(f"/tasks/{task}/schedule", {"date": "2026-09-21", "start_time": "15:00:00"})
    ).json()["data"]
    await me.post(f"/daily-actions/{action['id']}/checkins", {"new_status": "Started"})
    assert (await me.get(f"/tasks/{task}")).json()["data"]["status"] == "in_progress"
    await me.post(f"/daily-actions/{action['id']}/checkins", {"new_status": "Completed"})
    t = (await me.get(f"/tasks/{task}")).json()["data"]
    assert t["status"] == "completed"
    assert t["completed_at"] is not None


async def test_skipping_leaves_task_open(api: Api) -> None:
    me = await api.as_user("pat@example.com")
    task = await new_task(me)
    action = (
        await me.post(f"/tasks/{task}/schedule", {"date": "2026-09-21", "start_time": "15:00:00"})
    ).json()["data"]
    await me.post(
        f"/daily-actions/{action['id']}/checkins", {"new_status": "Skipped", "note": "meeting ran over"}
    )
    assert (await me.get(f"/tasks/{task}")).json()["data"]["status"] == "open"


async def test_complete_task_completes_scheduled_action_in_same_txn(api: Api) -> None:  # R3.10
    me = await api.as_user("pat@example.com")
    task = await new_task(me)
    action = (
        await me.post(f"/tasks/{task}/schedule", {"date": "2026-09-21", "start_time": "15:00:00"})
    ).json()["data"]
    r = await me.post(f"/tasks/{task}/complete", {"note": "done early"})
    body = r.json()["data"]
    assert body["task"]["status"] == "completed"
    assert body["daily_action"]["id"] == action["id"]
    assert body["daily_action"]["status"] == "Completed"
    history = (await me.get(f"/daily-actions/{action['id']}/checkins")).json()["data"]
    assert (history[-1]["new_status"], history[-1]["note"]) == ("Completed", "done early")
    assert (await me.post(f"/tasks/{task}/complete", {})).status_code == 409


async def test_complete_unscheduled_task(api: Api) -> None:
    me = await api.as_user("pat@example.com")
    task = await new_task(me)
    body = (await me.post(f"/tasks/{task}/complete", {})).json()["data"]
    assert body["task"]["status"] == "completed"
    assert body["daily_action"] is None


async def test_archiving_goal_cancels_future_task_actions(api: Api) -> None:
    me = await api.as_user("pat@example.com")
    goal = (await me.post("/goals", {"title": "Ship", "category": "Career"})).json()["data"]
    task = await new_task(me, goal_id=goal["id"])
    await me.post(f"/tasks/{task}/schedule", {"date": "2026-09-23", "start_time": "09:00:00"})
    await me.post(f"/goals/{goal['id']}/archive")
    (row,) = await actions(api)
    assert row.lifecycle_state == "cancelled"
