"""T4.3: progress recompute in the same transaction, history, outbox event (design §16.1)."""

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests.integration.api_harness import Api

BOOKS = {
    "title": "Read books",
    "metric_direction": "higher_is_better",
    "current_value": "3",
    "target_value": "12",
    "unit": "books",
    "target_date": "2026-12-31",
}
WEIGHT = {
    "title": "Lose weight",
    "metric_direction": "lower_is_better",
    "current_value": "100",
    "baseline_value": "100",
    "target_value": "80",
    "unit": "kg",
    "weight": "3",
    "target_date": "2026-12-31",
}


async def test_goal_progress_is_weighted_and_recomputed(api: Api) -> None:
    me = await api.as_user("hal@example.com")
    goal = (await me.post("/goals", {"title": "Better me", "category": "Personal"})).json()["data"]
    books = (await me.post(f"/goals/{goal['id']}/objectives", BOOKS)).json()["data"]
    assert books["progress"] == "25.00"
    assert (await me.get(f"/goals/{goal['id']}")).json()["data"]["progress"] == "25.00"

    weight = (await me.post(f"/goals/{goal['id']}/objectives", WEIGHT)).json()["data"]
    assert weight["progress"] == "0.00"
    # (25×1 + 0×3) / 4 = 6.25
    assert (await me.get(f"/goals/{goal['id']}")).json()["data"]["progress"] == "6.25"

    r = await me.post(f"/objectives/{weight['id']}/value", {"current_value": "90"})  # 50%
    assert r.json()["data"]["progress"] == "50.00"
    # (25×1 + 50×3) / 4 = 43.75
    assert (await me.get(f"/goals/{goal['id']}")).json()["data"]["progress"] == "43.75"


async def test_archiving_an_objective_changes_goal_progress(api: Api) -> None:
    me = await api.as_user("hal@example.com")
    goal = (await me.post("/goals", {"title": "Better me", "category": "Personal"})).json()["data"]
    await me.post(f"/goals/{goal['id']}/objectives", BOOKS)
    weight = (await me.post(f"/goals/{goal['id']}/objectives", WEIGHT)).json()["data"]
    assert (await me.get(f"/goals/{goal['id']}")).json()["data"]["progress"] == "6.25"

    await me.patch(f"/objectives/{weight['id']}", {"status": "archived"})
    assert (await me.get(f"/goals/{goal['id']}")).json()["data"]["progress"] == "25.00"  # only active count

    await me.delete(f"/objectives/{weight['id']}")
    assert (await me.get(f"/goals/{goal['id']}")).json()["data"]["progress"] == "25.00"


async def test_goal_without_active_objectives_shows_zero(api: Api) -> None:  # R1.7
    me = await api.as_user("hal@example.com")
    goal = (await me.post("/goals", {"title": "Better me", "category": "Personal"})).json()["data"]
    books = (await me.post(f"/goals/{goal['id']}/objectives", BOOKS)).json()["data"]
    await me.patch(f"/objectives/{books['id']}", {"status": "completed"})
    assert (await me.get(f"/goals/{goal['id']}")).json()["data"]["progress"] == "0.00"


async def test_value_changes_append_history_and_publish_event(api: Api) -> None:
    me = await api.as_user("hal@example.com")
    goal = (await me.post("/goals", {"title": "Better me", "category": "Personal"})).json()["data"]
    books = (await me.post(f"/goals/{goal['id']}/objectives", BOOKS)).json()["data"]
    await me.post(f"/objectives/{books['id']}/value", {"current_value": "6"})
    await me.patch(f"/objectives/{books['id']}", {"title": "Read more books"})  # no progress change

    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        history = list(
            (await s.execute(select(m.ObjectiveValueHistory).order_by(m.ObjectiveValueHistory.id))).scalars()
        )
        events = list(
            (
                await s.execute(
                    select(m.DomainEvent)
                    .where(m.DomainEvent.event_type == "objective.value_changed")
                    .order_by(m.DomainEvent.id)
                )
            ).scalars()
        )
    assert [(str(h.current_value), str(h.progress), h.changed_by) for h in history] == [
        ("3", "25.00", "User"),
        ("6", "50.00", "User"),
    ]
    assert [e.payload["progress"] for e in events] == ["25.00", "50.00"]
    assert events[-1].payload["goal_progress"] == "50.00"


async def test_failed_value_update_changes_nothing(api: Api) -> None:
    me = await api.as_user("hal@example.com")
    goal = (await me.post("/goals", {"title": "Better me", "category": "Personal"})).json()["data"]
    books = (await me.post(f"/goals/{goal['id']}/objectives", BOOKS)).json()["data"]
    r = await me.patch(f"/objectives/{books['id']}", {"current_value": "9", "target_value": "0"})
    assert r.status_code == 422
    assert (await me.get(f"/objectives/{books['id']}")).json()["data"]["current_value"] == "3"
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        rows = (await s.execute(select(m.ObjectiveValueHistory))).scalars().all()
    assert len(rows) == 1  # only the creation entry
