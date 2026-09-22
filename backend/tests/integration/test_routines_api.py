"""T5.1: routine templates and entries (R2.1–2.2)."""

from tests.integration.api_harness import Api


async def test_template_with_entries_and_stable_ids(api: Api) -> None:
    me = await api.as_user("jo@example.com")
    t = (await me.post("/routine-templates", {"title": "Weekdays", "active_days": [5, 1, 2, 3, 4]})).json()[
        "data"
    ]
    assert t["active_days"] == [1, 2, 3, 4, 5]
    e = (
        await me.post(
            f"/routine-templates/{t['id']}/entries",
            {"title": "Deep work", "start_time": "09:00:00", "end_time": "11:00:00"},
        )
    ).json()["data"]
    night = (
        await me.post(
            f"/routine-templates/{t['id']}/entries",
            {"title": "Night shift", "start_time": "23:00:00", "end_time": "01:00:00"},
        )
    ).json()["data"]
    assert night["crosses_midnight"] is True

    edited = (
        await me.patch(f"/routine-entries/{e['id']}", {"title": "Focus block", "end_time": "12:00:00"})
    ).json()
    assert edited["data"]["id"] == e["id"]  # stable Routine Entry ID across edits
    full = (await me.get(f"/routine-templates/{t['id']}")).json()["data"]
    assert [x["title"] for x in full["entries"]] == ["Focus block", "Night shift"]


async def test_one_template_per_weekday(api: Api) -> None:
    me = await api.as_user("jo@example.com")
    weekdays = (
        await me.post("/routine-templates", {"title": "Weekdays", "active_days": [1, 2, 3, 4, 5]})
    ).json()["data"]
    r = await me.post("/routine-templates", {"title": "Fridays", "active_days": [5, 6]})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "VALIDATION_ERROR")
    assert r.json()["error"]["details"]["conflicting_days"] == [5]
    weekend = (await me.post("/routine-templates", {"title": "Weekend", "active_days": [0, 6]})).json()[
        "data"
    ]
    r = await me.patch(f"/routine-templates/{weekend['id']}", {"active_days": [0, 1]})
    assert r.status_code == 422
    # Updating a template with its own days is not a conflict.
    assert (
        await me.patch(f"/routine-templates/{weekdays['id']}", {"active_days": [1, 2, 3, 4]})
    ).status_code == 200


async def test_invalid_days_and_zero_length_entries(api: Api) -> None:
    me = await api.as_user("jo@example.com")
    assert (await me.post("/routine-templates", {"title": "x", "active_days": [7]})).status_code == 422
    assert (await me.post("/routine-templates", {"title": "x", "active_days": [1, 1]})).status_code == 422
    t = (await me.post("/routine-templates", {"title": "x", "active_days": [1]})).json()["data"]
    r = await me.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "x", "start_time": "09:00:00", "end_time": "09:00:00"},
    )
    assert r.status_code == 422


async def test_entry_links_must_be_owned(api: Api) -> None:
    owner = await api.as_user("jo@example.com")
    goal = (await owner.post("/goals", {"title": "g", "category": "Life"})).json()["data"]
    other = await api.as_user("kim@example.com")
    t = (await other.post("/routine-templates", {"title": "x", "active_days": [1]})).json()["data"]
    r = await other.post(
        f"/routine-templates/{t['id']}/entries",
        {"title": "x", "start_time": "09:00:00", "end_time": "10:00:00", "goal_id": goal["id"]},
    )
    assert r.status_code == 404


async def test_delete_template_removes_entries(api: Api) -> None:
    me = await api.as_user("jo@example.com")
    t = (await me.post("/routine-templates", {"title": "x", "active_days": [1]})).json()["data"]
    e = (
        await me.post(
            f"/routine-templates/{t['id']}/entries",
            {"title": "x", "start_time": "09:00:00", "end_time": "10:00:00"},
        )
    ).json()["data"]
    assert (await me.delete(f"/routine-templates/{t['id']}")).status_code == 204
    assert (await me.get(f"/routine-entries/{e['id']}")).status_code == 404
    # The freed weekday can be reused.
    assert (await me.post("/routine-templates", {"title": "y", "active_days": [1]})).status_code == 201
