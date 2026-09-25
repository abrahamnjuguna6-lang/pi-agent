"""T6.1: the Promise Ledger view — filters, sorting and cursor pagination (R9.15, design §11.6)."""

from datetime import timedelta
from typing import Any

from tests.integration.api_harness import Api, AuthedClient


async def seed(api: Api, me: AuthedClient) -> dict[str, str]:
    """Three commitments: a kept career one, an open fitness one, and an unlinked one. The clock moves
    between them so `created_at` ordering is unambiguous."""
    ids: dict[str, str] = {}
    for title, category, due in (("Career", "Career", "2026-09-25"), ("Fitness", "Fitness", "2026-09-23")):
        goal = (await me.post("/goals", {"title": title, "category": category})).json()["data"]
        body = (
            await me.post(
                "/commitments",
                {
                    "title": f"{title} commitment",
                    "due_date": due,
                    "links": [{"entity_type": "Goal", "entity_id": goal["id"]}],
                },
            )
        ).json()["data"]
        ids[category] = body["id"]
        api.clock.set(api.clock.now() + timedelta(seconds=1))
    ids["none"] = (await me.post("/commitments", {"title": "Unlinked", "due_date": "2026-09-30"})).json()[
        "data"
    ]["id"]
    await me.post(f"/commitments/{ids['Career']}/keep")
    return ids


async def ledger(me: AuthedClient, **params: Any) -> list[dict[str, Any]]:
    r = await me.get("/commitments", **params)
    assert r.status_code == 200, r.text
    return list(r.json()["data"])


async def test_filters(api: Api) -> None:
    me = await api.as_user("ruth@example.com")
    ids = await seed(api, me)
    assert [c["id"] for c in await ledger(me, status="Kept")] == [ids["Career"]]
    assert [c["id"] for c in await ledger(me, goal_category="Fitness")] == [ids["Fitness"]]
    assert [c["id"] for c in await ledger(me, goal_category="Unlinked")] == [ids["none"]]
    assert {c["id"] for c in await ledger(me, due_from="2026-09-24")} == {ids["Career"], ids["none"]}
    assert {c["id"] for c in await ledger(me, due_to="2026-09-25")} == {ids["Career"], ids["Fitness"]}
    assert len(await ledger(me, due_from="2026-09-24", due_to="2026-09-26")) == 1


async def test_sorting(api: Api) -> None:
    me = await api.as_user("ruth@example.com")
    ids = await seed(api, me)
    assert [c["id"] for c in await ledger(me, sort="due_date", order="asc")] == [
        ids["Fitness"],
        ids["Career"],
        ids["none"],
    ]
    assert [c["status"] for c in await ledger(me, sort="status", order="asc")] == ["Kept", "Open", "Open"]
    assert [c["goal_category"] for c in await ledger(me, sort="goal_category", order="asc")] == [
        None,
        "Career",
        "Fitness",
    ]
    by_created = await ledger(me, sort="created_at", order="desc")
    assert [c["id"] for c in by_created] == [ids["none"], ids["Fitness"], ids["Career"]]


async def test_cursor_pagination(api: Api) -> None:
    me = await api.as_user("ruth@example.com")
    ids = await seed(api, me)
    r = await me.get("/commitments", sort="due_date", order="asc", limit=2)
    first = r.json()
    assert [c["id"] for c in first["data"]] == [ids["Fitness"], ids["Career"]]
    cursor = first["meta"]["next_cursor"]
    assert cursor is not None
    second = (await me.get("/commitments", sort="due_date", order="asc", limit=2, cursor=cursor)).json()
    assert [c["id"] for c in second["data"]] == [ids["none"]]
    assert second["meta"]["next_cursor"] is None

    descending = (await me.get("/commitments", sort="due_date", order="desc", limit=1)).json()
    assert [c["id"] for c in descending["data"]] == [ids["none"]]
    page = (
        await me.get(
            "/commitments", sort="due_date", order="desc", limit=1, cursor=descending["meta"]["next_cursor"]
        )
    ).json()
    assert [c["id"] for c in page["data"]] == [ids["Career"]]


async def test_invalid_cursor_and_limit(api: Api) -> None:
    me = await api.as_user("ruth@example.com")
    for params in ({"cursor": "not-a-cursor"}, {"limit": 0}, {"limit": 500}):
        r = await me.get("/commitments", **params)
        assert (r.status_code, r.json()["error"]["code"]) == (422, "VALIDATION_ERROR")


async def test_the_ledger_is_per_user(api: Api) -> None:
    me = await api.as_user("ruth@example.com")
    await seed(api, me)
    other = await api.as_user("sam@example.com")
    assert await ledger(other) == []
