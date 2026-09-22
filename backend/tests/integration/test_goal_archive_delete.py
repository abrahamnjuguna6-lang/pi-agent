"""T4.4: priority ordering, archive (read-only subtree), cascade delete, hierarchy (R1.14–1.16, R7.2)."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests import factories as f
from tests.integration.api_harness import Api, AuthedClient

OBJECTIVE = {
    "title": "Run distance",
    "metric_direction": "higher_is_better",
    "current_value": "0",
    "target_value": "42",
    "unit": "km",
    "target_date": "2027-04-01",
}


async def tree(me: AuthedClient) -> dict[str, str]:
    goal = (await me.post("/goals", {"title": "Marathon", "category": "Fitness"})).json()["data"]
    objective = (await me.post(f"/goals/{goal['id']}/objectives", OBJECTIVE)).json()["data"]
    project = (await me.post(f"/objectives/{objective['id']}/projects", {"title": "Training block"})).json()[
        "data"
    ]
    t1 = (await me.post("/tasks", {"title": "Buy shoes", "project_id": project["id"]})).json()["data"]
    t2 = (await me.post("/tasks", {"title": "Book race", "goal_id": goal["id"]})).json()["data"]
    return {
        "goal": goal["id"],
        "objective": objective["id"],
        "project": project["id"],
        "t1": t1["id"],
        "t2": t2["id"],
    }


async def test_goals_listed_in_priority_order(api: Api) -> None:
    me = await api.as_user("ivy@example.com")
    for body in (
        {"title": "Low", "category": "Life", "priority": 1},
        {"title": "Undated high", "category": "Life", "priority": 5},
        {"title": "Dated high", "category": "Life", "priority": 5, "target_date": "2027-01-01"},
    ):
        api.clock.set(api.clock.now() + timedelta(seconds=1))  # distinct created_at, as in real time
        await me.post("/goals", body)
    titles = [g["title"] for g in (await me.get("/goals", status="active")).json()["data"]]
    assert titles == ["Dated high", "Undated high", "Low"]

    low = next(g for g in (await me.get("/goals")).json()["data"] if g["title"] == "Low")
    await me.patch(f"/goals/{low['id']}/priority", {"priority": 5})
    titles = [g["title"] for g in (await me.get("/goals")).json()["data"]]
    # All priority 5 now: dated first, then the undated pair by created_at ("Low" was created first).
    assert titles == ["Dated high", "Low", "Undated high"]
    assert (await me.patch(f"/goals/{low['id']}/priority", {"priority": 0})).status_code == 422


async def test_archived_goal_subtree_is_read_only(api: Api) -> None:
    me = await api.as_user("ivy@example.com")
    ids = await tree(me)
    r = await me.post(f"/goals/{ids['goal']}/archive")
    assert r.json()["data"]["status"] == "archived"

    rejections = [
        await me.patch(f"/goals/{ids['goal']}", {"title": "renamed"}),
        await me.post(f"/goals/{ids['goal']}/objectives", OBJECTIVE),
        await me.patch(f"/objectives/{ids['objective']}", {"title": "x"}),
        await me.post(f"/objectives/{ids['objective']}/value", {"current_value": "5"}),
        await me.post(f"/objectives/{ids['objective']}/projects", {"title": "p"}),
        await me.patch(f"/projects/{ids['project']}", {"title": "x"}),
        await me.patch(f"/tasks/{ids['t1']}", {"title": "x"}),
        await me.patch(f"/tasks/{ids['t2']}", {"status": "completed"}),
        await me.post("/tasks", {"title": "new", "goal_id": ids["goal"]}),
        await me.delete(f"/tasks/{ids['t1']}"),
    ]
    for r in rejections:
        assert (r.status_code, r.json()["error"]["code"]) == (409, "GOAL_ARCHIVED"), r.text

    # History remains readable.
    assert (await me.get(f"/objectives/{ids['objective']}")).status_code == 200
    assert (await me.get(f"/tasks/{ids['t1']}")).status_code == 200
    assert (await me.get(f"/goals/{ids['goal']}/hierarchy")).status_code == 200


async def test_status_patch_cannot_archive_implicitly(api: Api) -> None:
    me = await api.as_user("ivy@example.com")
    goal = (await me.post("/goals", {"title": "x", "category": "Life"})).json()["data"]
    assert (await me.patch(f"/goals/{goal['id']}", {"status": "archived"})).status_code == 422


async def test_delete_requires_cascade_confirmation_with_counts(api: Api) -> None:
    me = await api.as_user("ivy@example.com")
    ids = await tree(me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        goal = (await s.execute(select(m.Goal).where(m.Goal.id == uuid.UUID(ids["goal"])))).scalar_one()
        s.add(f.HabitFactory(user_id=goal.user_id, goal_id=goal.id))

    r = await me.delete(f"/goals/{ids['goal']}")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "CASCADE_CONFIRMATION_REQUIRED")
    assert r.json()["error"]["details"] == {
        "objectives": 1,
        "active_objectives": 1,
        "projects": 1,
        "tasks": 2,
        "habits": 1,
    }
    assert (await me.get(f"/goals/{ids['goal']}")).status_code == 200  # nothing deleted

    r = await me.delete(f"/goals/{ids['goal']}", confirm_cascade="true")
    assert r.status_code == 200
    for path in (
        f"/goals/{ids['goal']}",
        f"/objectives/{ids['objective']}",
        f"/projects/{ids['project']}",
        f"/tasks/{ids['t1']}",
        f"/tasks/{ids['t2']}",
    ):
        assert (await me.get(path)).status_code == 404, path


async def test_goal_without_objectives_or_projects_deletes_directly(api: Api) -> None:
    me = await api.as_user("ivy@example.com")
    goal = (await me.post("/goals", {"title": "Someday", "category": "Life"})).json()["data"]
    assert (await me.delete(f"/goals/{goal['id']}")).status_code == 200
    assert (await me.get(f"/goals/{goal['id']}")).status_code == 404


async def test_delete_preserves_daily_actions_and_checkins(api: Api) -> None:
    me = await api.as_user("ivy@example.com")
    ids = await tree(me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        user = (await s.execute(select(m.Task.user_id).where(m.Task.id == uuid.UUID(ids["t1"])))).scalar_one()
        owner = (await s.execute(select(m.User).where(m.User.id == user))).scalar_one()
        await f.make_daily_action(s, owner, source_type="TASK", source_id=uuid.UUID(ids["t1"]))

    await me.delete(f"/goals/{ids['goal']}", confirm_cascade="true")
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        actions = (await s.execute(select(func.count()).select_from(m.DailyAction))).scalar_one()
        checkins = (await s.execute(select(func.count()).select_from(m.CheckinRecord))).scalar_one()
    assert (actions, checkins) == (1, 1)


async def test_hierarchy_tree(api: Api) -> None:  # R1.16
    me = await api.as_user("ivy@example.com")
    ids = await tree(me)
    root = (await me.get(f"/goals/{ids['goal']}/hierarchy")).json()["data"]
    assert (root["kind"], root["title"]) == ("goal", "Marathon")
    kinds = sorted(child["kind"] for child in root["children"])
    assert kinds == ["objective", "task"]  # goal-linked task hangs off the goal
    objective = next(c for c in root["children"] if c["kind"] == "objective")
    project = objective["children"][0]
    assert (project["kind"], project["title"]) == ("project", "Training block")
    assert [c["title"] for c in project["children"]] == ["Buy shoes"]
