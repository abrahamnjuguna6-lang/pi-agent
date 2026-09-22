"""T5.4: check-ins — history, rejection leaves no record, concurrency, event on commit only (R3)."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from lifeos.db import models as m
from lifeos.db.session import system_session, user_session
from lifeos.domain.errors import DomainError
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import user_id_of


async def manual(me: AuthedClient) -> str:
    r = await me.post(
        "/daily-actions",
        {"title": "Write report", "date": "2026-09-21", "start_time": "14:00:00", "end_time": "15:00:00"},
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


async def test_full_lifecycle_and_history(api: Api) -> None:
    me = await api.as_user("nia@example.com")
    action = await manual(me)
    r = await me.post(f"/daily-actions/{action}/checkins", {"new_status": "Started"})
    assert r.status_code == 201
    r = await me.post(f"/daily-actions/{action}/checkins", {"new_status": "Completed", "note": "Shipped it"})
    body = r.json()["data"]
    assert body["daily_action"]["status"] == "Completed"
    assert body["daily_action"]["completed_at"] is not None  # R3.5
    history = (await me.get(f"/daily-actions/{action}/checkins")).json()["data"]
    assert [(h["previous_status"], h["new_status"], h["transition_source"]) for h in history] == [
        (None, "Planned", "System"),
        ("Planned", "Started", "User"),
        ("Started", "Completed", "User"),
    ]
    assert history[-1]["note"] == "Shipped it"


async def test_rejected_transitions_create_no_record(api: Api) -> None:
    me = await api.as_user("nia@example.com")
    action = await manual(me)
    await me.post(f"/daily-actions/{action}/checkins", {"new_status": "Completed"})
    for status in ("Started", "Skipped", "Completed"):
        r = await me.post(f"/daily-actions/{action}/checkins", {"new_status": status, "note": "x"})
        assert (r.status_code, r.json()["error"]["code"]) == (409, "INVALID_TRANSITION")
    assert len((await me.get(f"/daily-actions/{action}/checkins")).json()["data"]) == 2  # R3.3


async def test_skip_requires_reason_via_api(api: Api) -> None:
    me = await api.as_user("nia@example.com")
    action = await manual(me)
    r = await me.post(f"/daily-actions/{action}/checkins", {"new_status": "Skipped"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "SKIP_REASON_REQUIRED")
    r = await me.post(f"/daily-actions/{action}/checkins", {"new_status": "Skipped", "note": "Migraine"})
    assert r.status_code == 201
    assert r.json()["data"]["checkin"]["note"] == "Migraine"


async def test_concurrent_transitions_one_wins(api: Api) -> None:
    me = await api.as_user("nia@example.com")
    action = uuid.UUID(await manual(me))
    uid = await user_id_of(api, me)

    async def attempt(status: str) -> str:
        try:
            async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
                await api.container.checkins.transition(s, uid, action, status, "reason")  # type: ignore[arg-type]
                await asyncio.sleep(0.05)  # hold the row lock while the other request waits
            return "ok"
        except DomainError as exc:
            return exc.code

    results = sorted(await asyncio.gather(attempt("Completed"), attempt("Skipped")))
    assert results == ["INVALID_TRANSITION", "ok"]
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        count = (
            await s.execute(select(func.count()).where(m.CheckinRecord.daily_action_id == action))
        ).scalar_one()
    assert count == 2  # initial + exactly one transition


async def test_event_published_only_on_commit(api: Api) -> None:
    me = await api.as_user("nia@example.com")
    action = uuid.UUID(await manual(me))
    uid = await user_id_of(api, me)
    with pytest.raises(RuntimeError):
        async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
            await api.container.checkins.transition(s, uid, action, "Completed")
            raise RuntimeError("request failed after the transition")
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        events = (
            await s.execute(
                select(func.count()).where(m.DomainEvent.event_type == "daily_action.status_changed")
            )
        ).scalar_one()
        status = (
            await s.execute(select(m.DailyAction.status).where(m.DailyAction.id == action))
        ).scalar_one()
    assert (events, status) == (0, "Planned")

    await me.post(f"/daily-actions/{action}/checkins", {"new_status": "Completed"})
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        event = (
            await s.execute(
                select(m.DomainEvent).where(m.DomainEvent.event_type == "daily_action.status_changed")
            )
        ).scalar_one()
    assert event.payload["new_status"] == "Completed"
    assert event.payload["daily_action_id"] == str(action)


async def test_other_users_actions_are_404(api: Api) -> None:
    owner = await api.as_user("nia@example.com")
    action = await manual(owner)
    intruder = await api.as_user("oz@example.com")
    assert (await intruder.get(f"/daily-actions/{action}")).status_code == 404
    r = await intruder.post(f"/daily-actions/{action}/checkins", {"new_status": "Completed"})
    assert r.status_code == 404


async def test_started_action_under_archived_goal_is_read_only(api: Api) -> None:  # R1.14
    me = await api.as_user("nia@example.com")
    goal = (await me.post("/goals", {"title": "Ship", "category": "Career"})).json()["data"]
    task = (await me.post("/tasks", {"title": "Draft", "goal_id": goal["id"]})).json()["data"]
    action = (
        await me.post(f"/tasks/{task['id']}/schedule", {"date": "2026-09-21", "start_time": "13:00:00"})
    ).json()["data"]
    await me.post(f"/daily-actions/{action['id']}/checkins", {"new_status": "Started"})
    await me.post(f"/goals/{goal['id']}/archive")  # started actions are not cancelled by archiving
    r = await me.post(f"/daily-actions/{action['id']}/checkins", {"new_status": "Completed"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "GOAL_ARCHIVED")
