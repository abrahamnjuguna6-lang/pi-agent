"""T5.2: habit CRUD, recurrence validation, pauses (R1.10, R1.12)."""

import pytest

from tests.integration.api_harness import Api


async def test_habit_crud(api: Api) -> None:
    me = await api.as_user("max@example.com")
    goal = (await me.post("/goals", {"title": "Health", "category": "Fitness"})).json()["data"]
    habit = (
        await me.post(
            "/habits",
            {
                "title": "Gym",
                "goal_id": goal["id"],
                "recurrence_type": "weekly",
                "recurrence_days": [5, 1, 3],
                "frequency_target": 3,
            },
        )
    ).json()["data"]
    assert habit["recurrence_days"] == [1, 3, 5]
    assert habit["paused"] is False
    patched = (await me.patch(f"/habits/{habit['id']}", {"frequency_target": 2})).json()["data"]
    assert patched["frequency_target"] == 2
    assert len((await me.get("/habits")).json()["data"]) == 1
    assert (await me.delete(f"/habits/{habit['id']}")).status_code == 204
    assert (await me.get(f"/habits/{habit['id']}")).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"recurrence_type": "weekly"},  # days required
        {"recurrence_type": "custom", "recurrence_days": [8]},
        {"recurrence_type": "weekly", "recurrence_days": [1, 1]},
        {"recurrence_type": "weekly", "recurrence_days": [1, 2], "frequency_target": 3},  # target > days
        {"recurrence_type": "daily", "start_date": "2026-10-01", "end_date": "2026-09-01"},
    ],
)
async def test_recurrence_validation(api: Api, body: dict[str, object]) -> None:
    me = await api.as_user("max@example.com")
    goal = (await me.post("/goals", {"title": "Health", "category": "Fitness"})).json()["data"]
    r = await me.post("/habits", {"title": "x", "goal_id": goal["id"], **body})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "VALIDATION_ERROR")


async def test_habit_requires_goal_or_project(api: Api) -> None:  # R1.10 "beneath a Goal or Project"
    me = await api.as_user("max@example.com")
    r = await me.post("/habits", {"title": "x", "recurrence_type": "daily"})
    assert r.status_code == 422


async def test_pause_rules(api: Api) -> None:
    me = await api.as_user("max@example.com")
    goal = (await me.post("/goals", {"title": "Health", "category": "Fitness"})).json()["data"]
    habit = (
        await me.post("/habits", {"title": "x", "goal_id": goal["id"], "recurrence_type": "daily"})
    ).json()["data"]
    assert (
        await me.post(f"/habits/{habit['id']}/pause", {"starts_on": "2026-01-01"})
    ).status_code == 422  # past
    assert (await me.post(f"/habits/{habit['id']}/pause", {})).status_code == 200
    r = await me.post(f"/habits/{habit['id']}/pause", {})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "INVALID_TRANSITION")


async def test_habit_under_archived_goal_is_read_only(api: Api) -> None:
    me = await api.as_user("max@example.com")
    goal = (await me.post("/goals", {"title": "Health", "category": "Fitness"})).json()["data"]
    habit = (
        await me.post("/habits", {"title": "x", "goal_id": goal["id"], "recurrence_type": "daily"})
    ).json()["data"]
    await me.post(f"/goals/{goal['id']}/archive")
    r = await me.patch(f"/habits/{habit['id']}", {"title": "y"})
    assert (r.status_code, r.json()["error"]["code"]) == (409, "GOAL_ARCHIVED")
