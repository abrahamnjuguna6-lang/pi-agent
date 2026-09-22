"""T4.2: Goal / Objective / Project / Task CRUD, ownership, validation (R1.1–1.3, R1.8–1.9, R15.1)."""

from datetime import timedelta

import pytest

from tests.integration.api_harness import Api, AuthedClient

OBJECTIVE = {
    "title": "Read books",
    "metric_direction": "higher_is_better",
    "current_value": "0",
    "target_value": "12",
    "unit": "books",
    "target_date": "2026-12-31",
}


async def make_tree(me: AuthedClient) -> dict[str, str]:
    goal = (await me.post("/goals", {"title": "Grow as an engineer", "category": "Career"})).json()["data"]
    objective = (await me.post(f"/goals/{goal['id']}/objectives", OBJECTIVE)).json()["data"]
    project = (await me.post(f"/objectives/{objective['id']}/projects", {"title": "Reading plan"})).json()[
        "data"
    ]
    task = (
        await me.post("/tasks", {"title": "Buy book", "project_id": project["id"], "goal_id": goal["id"]})
    ).json()["data"]
    return {"goal": goal["id"], "objective": objective["id"], "project": project["id"], "task": task["id"]}


async def test_crud_happy_path(api: Api) -> None:
    me = await api.as_user("fay@example.com")
    ids = await make_tree(me)

    goal = (await me.get(f"/goals/{ids['goal']}")).json()["data"]
    assert (goal["title"], goal["category"], goal["priority"], goal["status"]) == (
        "Grow as an engineer",
        "Career",
        3,
        "active",
    )
    assert goal["progress"] == "0.00"

    r = await me.patch(
        f"/goals/{ids['goal']}", {"description": "Senior by 2027", "target_date": "2027-06-30"}
    )
    assert r.json()["data"]["description"] == "Senior by 2027"

    objective = (await me.get(f"/objectives/{ids['objective']}")).json()["data"]
    assert (objective["unit"], objective["weight"], objective["progress"]) == ("books", "1", "0.00")

    project = (await me.patch(f"/projects/{ids['project']}", {"status": "completed"})).json()["data"]
    assert project["status"] == "completed"

    task = (await me.patch(f"/tasks/{ids['task']}", {"status": "completed"})).json()["data"]
    assert task["status"] == "completed"
    assert task["completed_at"] is not None

    assert (await me.delete(f"/tasks/{ids['task']}")).status_code == 204
    assert (await me.get(f"/tasks/{ids['task']}")).status_code == 404


async def test_adhoc_task_without_links(api: Api) -> None:  # R3.7
    me = await api.as_user("fay@example.com")
    r = await me.post("/tasks", {"title": "Call the dentist", "due_date": "2026-09-30"})
    assert r.status_code == 201
    assert (r.json()["data"]["project_id"], r.json()["data"]["goal_id"]) == (None, None)


async def test_other_users_resources_are_404(api: Api) -> None:
    owner = await api.as_user("fay@example.com")
    ids = await make_tree(owner)
    intruder = await api.as_user("gil@example.com")

    for path in (
        f"/goals/{ids['goal']}",
        f"/objectives/{ids['objective']}",
        f"/projects/{ids['project']}",
        f"/tasks/{ids['task']}",
        f"/goals/{ids['goal']}/hierarchy",
        f"/goals/{ids['goal']}/objectives",
    ):
        r = await intruder.get(path)
        assert (r.status_code, r.json()["error"]["code"]) == (404, "NOT_FOUND"), path

    assert (await intruder.patch(f"/goals/{ids['goal']}", {"title": "mine now"})).status_code == 404
    assert (await intruder.delete(f"/tasks/{ids['task']}")).status_code == 404
    assert (await intruder.post(f"/goals/{ids['goal']}/objectives", OBJECTIVE)).status_code == 404
    assert (await intruder.post("/tasks", {"title": "x", "goal_id": ids["goal"]})).status_code == 404
    assert (await owner.get(f"/goals/{ids['goal']}")).json()["data"]["title"] == "Grow as an engineer"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"target_value": "0"}, "target_value"),
        ({"metric_direction": "lower_is_better", "target_value": "20"}, "baseline_value"),
        (
            {"metric_direction": "lower_is_better", "target_value": "20", "baseline_value": "20"},
            "baseline_value",
        ),
    ],
)
async def test_objective_range_validation_saves_nothing(
    api: Api, overrides: dict[str, str], field: str
) -> None:
    me = await api.as_user("fay@example.com")
    goal = (await me.post("/goals", {"title": "Health", "category": "Fitness"})).json()["data"]
    r = await me.post(f"/goals/{goal['id']}/objectives", {**OBJECTIVE, **overrides})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "VALIDATION_ERROR")
    assert r.json()["error"]["details"]["field"] == field
    assert (await me.get(f"/goals/{goal['id']}/objectives")).json()["data"] == []


async def test_objective_update_revalidates_range(api: Api) -> None:
    me = await api.as_user("fay@example.com")
    ids = await make_tree(me)
    r = await me.patch(f"/objectives/{ids['objective']}", {"target_value": "0"})
    assert r.status_code == 422
    assert (await me.get(f"/objectives/{ids['objective']}")).json()["data"]["target_value"] == "12"


async def test_objective_target_date_required(api: Api) -> None:  # R1.2
    me = await api.as_user("fay@example.com")
    goal = (await me.post("/goals", {"title": "Health", "category": "Fitness"})).json()["data"]
    body = {k: v for k, v in OBJECTIVE.items() if k != "target_date"}
    r = await me.post(f"/goals/{goal['id']}/objectives", body)
    assert r.status_code == 422


async def test_goal_validation(api: Api) -> None:
    me = await api.as_user("fay@example.com")
    assert (await me.post("/goals", {"title": "x", "category": "Hobbies"})).status_code == 422
    assert (await me.post("/goals", {"title": "x", "category": "Life", "priority": 6})).status_code == 422
    assert (await me.post("/goals", {"title": "", "category": "Life"})).status_code == 422


async def test_task_cursor_pagination(api: Api) -> None:
    me = await api.as_user("fay@example.com")
    for n in range(5):
        api.clock.set(api.clock.now() + timedelta(seconds=1))  # distinct created_at, as in real time
        await me.post("/tasks", {"title": f"t{n}"})
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        body = (await me.get("/tasks", **params)).json()
        seen += [t["title"] for t in body["data"]]
        cursor = body["meta"]["next_cursor"]
        if cursor is None:
            break
    assert seen == ["t0", "t1", "t2", "t3", "t4"]
    assert (await me.get("/tasks", cursor="garbage")).status_code == 422
    assert (await me.get("/tasks", limit=500)).status_code == 422


async def test_task_filters(api: Api) -> None:
    me = await api.as_user("fay@example.com")
    ids = await make_tree(me)
    await me.post("/tasks", {"title": "loose"})
    by_project = (await me.get("/tasks", project_id=ids["project"])).json()["data"]
    assert [t["title"] for t in by_project] == ["Buy book"]
    await me.patch(f"/tasks/{ids['task']}", {"status": "completed"})
    open_tasks = (await me.get("/tasks", status="open")).json()["data"]
    assert [t["title"] for t in open_tasks] == ["loose"]


async def test_create_goal_is_idempotent(api: Api) -> None:
    me = await api.as_user("fay@example.com")
    body = {"title": "Once", "category": "Life"}
    first = await me.post("/goals", body, key="create-goal-once")
    second = await me.post("/goals", body, key="create-goal-once")
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert len((await me.get("/goals")).json()["data"]) == 1
