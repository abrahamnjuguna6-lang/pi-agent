"""T8.3: lineage walks from an item up to its Goal (design §16.9; R13.7).

These run against the database because the walk is the query: every source type takes a different
path through the schema.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from lifeos.db.session import user_session
from lifeos.domain.errors import NotFoundError
from lifeos.domain.lineage import LineageService
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import actions, generate, goal_with_habit, user_id_of

EMAIL = "vera@example.com"
MON = date(2026, 9, 21)
lineage_service = LineageService()


async def chain_of(
    api: Api, me: AuthedClient, target_type: str, target_id: uuid.UUID
) -> list[tuple[str, str]]:
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        result = await lineage_service.lineage(s, uid, target_type, target_id)  # type: ignore[arg-type]
    return [(node.type, node.title) for node in result.chain]


async def lineage_of(api: Api, me: AuthedClient, target_type: str, target_id: uuid.UUID) -> Any:
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        return await lineage_service.lineage(s, uid, target_type, target_id)  # type: ignore[arg-type]


async def goal_tree(me: AuthedClient) -> dict[str, Any]:
    """Goal → Objective → Project → Task, with the Task scheduled as a Daily Action."""
    goal = (await me.post("/goals", {"title": "Ship the product", "category": "Career"})).json()["data"]
    objective = (
        await me.post(
            f"/goals/{goal['id']}/objectives",
            {
                "title": "Ten paying customers",
                "metric_direction": "higher_is_better",
                "current_value": "0",
                "target_value": "10",
                "unit": "customers",
                "target_date": "2026-12-31",
            },
        )
    ).json()["data"]
    project = (await me.post(f"/objectives/{objective['id']}/projects", {"title": "Launch the beta"})).json()[
        "data"
    ]
    task = (await me.post("/tasks", {"title": "Write the landing page", "project_id": project["id"]})).json()[
        "data"
    ]
    action = (
        await me.post(
            f"/tasks/{task['id']}/schedule",
            {"date": MON.isoformat(), "start_time": "09:00:00", "end_time": "10:00:00"},
        )
    ).json()["data"]
    return {"goal": goal, "objective": objective, "project": project, "task": task, "action": action}


async def test_daily_action_through_task_project_objective_to_goal(api: Api) -> None:  # R13.7
    me = await api.as_user(EMAIL)
    tree = await goal_tree(me)
    chain = await chain_of(api, me, "daily_action", uuid.UUID(tree["action"]["id"]))
    assert chain == [
        ("daily_action", "Write the landing page"),
        ("task", "Write the landing page"),
        ("project", "Launch the beta"),
        ("objective", "Ten paying customers"),
        ("goal", "Ship the product"),
    ]
    result = await lineage_of(api, me, "daily_action", uuid.UUID(tree["action"]["id"]))
    assert (result.unlinked, result.goal_category) == (False, "Career")
    assert result.goal is not None
    assert result.goal.priority is not None


async def test_habit_action_reaches_its_goal_directly(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await goal_with_habit(me)
    await generate(api, me, MON)
    (action,) = await actions(api, date=MON, source_type="HABIT")
    assert await chain_of(api, me, "daily_action", action.id) == [
        ("daily_action", "Run"),
        ("habit", "Run"),
        ("goal", "Health"),
    ]


async def test_routine_entry_linked_to_a_habit_linked_to_a_goal(api: Api) -> None:  # design §16.9
    me = await api.as_user(EMAIL)
    _, habit = await goal_with_habit(me)
    template = (
        await me.post("/routine-templates", {"title": "Weekdays", "active_days": [0, 1, 2, 3, 4, 5, 6]})
    ).json()["data"]
    entry = (
        await me.post(
            f"/routine-templates/{template['id']}/entries",
            {
                "title": "Morning run slot",
                "start_time": "06:00:00",
                "end_time": "06:45:00",
                "habit_id": habit["id"],
            },
        )
    ).json()["data"]
    await generate(api, me, MON)
    (action,) = await actions(api, date=MON, source_type="ROUTINE_ENTRY")
    assert await chain_of(api, me, "daily_action", action.id) == [
        ("daily_action", "Morning run slot"),
        ("routine_entry", "Morning run slot"),
        ("habit", "Run"),
        ("goal", "Health"),
    ]
    assert await chain_of(api, me, "routine_entry", uuid.UUID(entry["id"])) == [
        ("routine_entry", "Morning run slot"),
        ("habit", "Run"),
        ("goal", "Health"),
    ]


async def test_a_routine_entry_may_point_straight_at_a_goal(api: Api) -> None:
    me = await api.as_user(EMAIL)
    goal = (await me.post("/goals", {"title": "Write daily", "category": "Personal"})).json()["data"]
    template = (
        await me.post("/routine-templates", {"title": "Mornings", "active_days": [0, 1, 2, 3, 4, 5, 6]})
    ).json()["data"]
    entry = (
        await me.post(
            f"/routine-templates/{template['id']}/entries",
            {
                "title": "Morning pages",
                "start_time": "06:00:00",
                "end_time": "06:30:00",
                "goal_id": goal["id"],
            },
        )
    ).json()["data"]
    assert await chain_of(api, me, "routine_entry", uuid.UUID(entry["id"])) == [
        ("routine_entry", "Morning pages"),
        ("goal", "Write daily"),
    ]


async def test_a_manual_action_is_unlinked(api: Api) -> None:  # design §16.9
    me = await api.as_user(EMAIL)
    action = (
        await me.post(
            "/daily-actions",
            {
                "title": "Call the bank",
                "date": MON.isoformat(),
                "start_time": "11:00:00",
                "end_time": "11:30:00",
            },
        )
    ).json()["data"]
    result = await lineage_of(api, me, "daily_action", uuid.UUID(action["id"]))
    assert [node.type for node in result.chain] == ["daily_action"]
    assert (result.unlinked, result.goal, result.goal_category) == (True, None, None)
    assert result.as_json()["marker"] == "unlinked"


async def test_an_unlinked_routine_entry_stops_at_itself(api: Api) -> None:
    me = await api.as_user(EMAIL)
    template = (await me.post("/routine-templates", {"title": "Mornings", "active_days": [1]})).json()["data"]
    entry = (
        await me.post(
            f"/routine-templates/{template['id']}/entries",
            {"title": "Coffee", "start_time": "07:00:00", "end_time": "07:15:00"},
        )
    ).json()["data"]
    result = await lineage_of(api, me, "routine_entry", uuid.UUID(entry["id"]))
    assert (result.unlinked, [n.type for n in result.chain]) == (True, ["routine_entry"])


async def test_a_deleted_goal_makes_the_chain_unlinked(api: Api) -> None:
    me = await api.as_user(EMAIL)
    goal_id, _habit = await goal_with_habit(me)
    await generate(api, me, MON)
    (action,) = await actions(api, date=MON, source_type="HABIT")
    assert (await me.delete(f"/goals/{goal_id}", confirm_cascade=True)).status_code in (200, 204)
    result = await lineage_of(api, me, "daily_action", action.id)
    assert result.unlinked is True  # the habit row is soft-deleted with its goal
    assert [node.type for node in result.chain] == ["daily_action"]


async def test_another_users_item_is_not_found(api: Api) -> None:
    me = await api.as_user(EMAIL)
    tree = await goal_tree(me)
    other = await api.as_user("mallory@example.com")
    other_id = await user_id_of(api, other)
    async with user_session(other_id, sessionmaker=api.container.sessionmaker) as s:
        with pytest.raises(NotFoundError) as exc:  # the walk never crosses a user boundary
            await lineage_service.lineage(s, other_id, "daily_action", uuid.UUID(tree["action"]["id"]))
    assert exc.value.code == "NOT_FOUND"
