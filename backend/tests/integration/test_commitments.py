"""T6.1: Promise Ledger through the API — creation, links, automatic completion, cancelled links (R9)."""

from datetime import date
from typing import Any

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import user_id_of

MON = date(2026, 9, 21)  # the frozen clock is 2026-09-21 12:00 UTC
DUE = "2026-09-23"


async def manual(me: AuthedClient, title: str = "Deep work", start: str = "14:00:00") -> str:
    r = await me.post(
        "/daily-actions",
        {"title": title, "date": MON.isoformat(), "start_time": start, "end_time": "23:00:00"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["data"]["id"])


async def commit(me: AuthedClient, **body: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": "Ship the proposal", "due_date": DUE} | body
    r = await me.post("/commitments", payload)
    assert r.status_code == 201, r.text
    return dict(r.json()["data"])


def link(action_id: str) -> dict[str, str]:
    return {"entity_type": "DailyAction", "entity_id": action_id}


async def complete(me: AuthedClient, action_id: str) -> None:
    r = await me.post(f"/daily-actions/{action_id}/checkins", {"new_status": "Completed"})
    assert r.status_code == 201, r.text


async def get(me: AuthedClient, commitment_id: str) -> dict[str, Any]:
    r = await me.get(f"/commitments/{commitment_id}")
    assert r.status_code == 200, r.text
    return dict(r.json()["data"])


# --------------------------------------------------------------------------- creation (R9.1–9.3)


async def test_create_unlinked_commitment_is_explicit(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    body = await commit(me)
    assert (body["completion_condition"], body["status"], body["goal_category"]) == ("explicit", "Open", None)
    assert body["current_due_date"] == body["due_date"] == DUE
    detail = await get(me, body["id"])
    assert [(e["event_type"], e["new_status"], e["actor"]) for e in detail["events"]] == [
        ("created", "Open", "User")
    ]


async def test_goal_category_is_derived_through_lineage(api: Api) -> None:  # §11.6
    me = await api.as_user("ada@example.com")
    goal = (await me.post("/goals", {"title": "Fitness", "category": "Fitness"})).json()["data"]
    body = await commit(me, links=[{"entity_type": "Goal", "entity_id": goal["id"]}])
    assert (body["goal_category"], body["completion_condition"]) == ("Fitness", "explicit")


async def test_condition_is_validated_against_the_links(api: Api) -> None:  # R9.2
    me = await api.as_user("ada@example.com")
    one, two = await manual(me), await manual(me, "Review", "16:00:00")
    assert (await commit(me, links=[link(one)]))["completion_condition"] == "single"
    assert (await commit(me, links=[link(one), link(two)], completion_condition="any"))[
        "completion_condition"
    ] == "any"
    r = await me.post("/commitments", {"title": "x", "due_date": DUE, "links": [link(one), link(two)]})
    assert (r.status_code, r.json()["error"]["details"]["field"]) == (422, "completion_condition")


async def test_creation_rejects_bad_input(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    action = await manual(me)
    past = await me.post("/commitments", {"title": "x", "due_date": "2026-09-20"})
    assert (past.status_code, past.json()["error"]["details"]["field"]) == (422, "due_date")
    duplicate = await me.post(
        "/commitments", {"title": "x", "due_date": DUE, "links": [link(action), link(action)]}
    )
    assert (duplicate.status_code, duplicate.json()["error"]["details"]["field"]) == (422, "links")
    await complete(me, action)
    resolved = await me.post("/commitments", {"title": "x", "due_date": DUE, "links": [link(action)]})
    assert (resolved.status_code, resolved.json()["error"]["details"]["field"]) == (422, "links")


async def test_links_to_other_users_entities_are_not_found(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    other = await api.as_user("mallory@example.com")
    goal = (await other.post("/goals", {"title": "Theirs", "category": "Career"})).json()["data"]
    r = await me.post(
        "/commitments",
        {"title": "x", "due_date": DUE, "links": [{"entity_type": "Goal", "entity_id": goal["id"]}]},
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- automatic completion (R9.5–9.6)


async def test_single_linked_action_completes_the_commitment(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    action = await manual(me)
    body = await commit(me, links=[link(action)])
    await complete(me, action)
    detail = await get(me, body["id"])
    assert (detail["status"], detail["kept_at"] is not None) == ("Kept", True)
    assert [e["event_type"] for e in detail["events"]] == ["created", "kept"]
    assert [e["actor"] for e in detail["events"]] == ["User", "System"]  # automatic (R9.6)
    score = (await me.get("/integrity-score")).json()["data"]
    assert (score["kept"], score["score"]) == (1, "100.0")  # snapshot refreshed in the same transaction


async def test_all_condition_needs_every_action(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    one, two = await manual(me), await manual(me, "Review", "16:00:00")
    body = await commit(me, links=[link(one), link(two)], completion_condition="all")
    await complete(me, one)
    assert (await get(me, body["id"]))["status"] == "Open"
    await complete(me, two)
    assert (await get(me, body["id"]))["status"] == "Kept"


async def test_any_condition_needs_one_action(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    one, two = await manual(me), await manual(me, "Review", "16:00:00")
    body = await commit(me, links=[link(one), link(two)], completion_condition="any")
    await complete(me, two)
    assert (await get(me, body["id"]))["status"] == "Kept"


async def test_explicit_commitments_ignore_daily_actions(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    action = await manual(me)
    body = await commit(me)  # unlinked
    await complete(me, action)
    assert (await get(me, body["id"]))["status"] == "Open"
    r = await me.post(f"/commitments/{body['id']}/keep")
    assert (r.status_code, r.json()["data"]["status"]) == (200, "Kept")


# --------------------------------------------------------------------------- explicit actions


async def test_keep_and_cancel_are_terminal(api: Api) -> None:  # R9.4
    me = await api.as_user("ada@example.com")
    kept, cancelled = await commit(me), await commit(me)
    assert (await me.post(f"/commitments/{kept['id']}/keep")).status_code == 200
    assert (await me.post(f"/commitments/{cancelled['id']}/cancel")).json()["data"]["status"] == "Cancelled"
    for commitment_id, action in ((kept["id"], "cancel"), (cancelled["id"], "keep")):
        r = await me.post(f"/commitments/{commitment_id}/{action}")
        assert (r.status_code, r.json()["error"]["code"]) == (409, "INVALID_COMMITMENT_TRANSITION")
    score = (await me.get("/integrity-score")).json()["data"]
    assert (score["kept"], score["broken"], score["score"]) == (1, 0, "100.0")  # cancelled excluded (R9.12)


async def test_unknown_commitment_is_404(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    other = await api.as_user("mallory@example.com")
    theirs = (await other.post("/commitments", {"title": "Theirs", "due_date": DUE})).json()["data"]
    assert (await me.get(f"/commitments/{theirs['id']}")).status_code == 404
    assert (await me.post(f"/commitments/{theirs['id']}/keep")).status_code == 404


# --------------------------------------------------------------------------- linked action changes (R9.8–9.9)


async def test_rescheduling_a_linked_action_changes_nothing(api: Api) -> None:  # R9.8
    me = await api.as_user("ada@example.com")
    action = await manual(me)
    body = await commit(me, links=[link(action)])
    r = await me.patch(
        f"/daily-actions/{action}/schedule",
        {"date": "2026-09-22", "start_time": "09:00:00", "end_time": "10:00:00"},
    )
    assert r.status_code == 200, r.text
    detail = await get(me, body["id"])
    assert (detail["status"], detail["current_due_date"], detail["completion_condition"]) == (
        "Open",
        DUE,
        "single",
    )
    assert [e["event_type"] for e in detail["events"]] == ["created"]
    assert [link_row["removed_at"] for link_row in detail["links"]] == [None]


async def test_cancelling_the_only_link_keeps_the_commitment(api: Api) -> None:  # R9.9
    me = await api.as_user("ada@example.com")
    action = await manual(me)
    body = await commit(me, links=[link(action)])
    assert (await me.post(f"/daily-actions/{action}/cancel", {"reason": "not today"})).status_code == 200
    detail = await get(me, body["id"])
    assert (detail["status"], detail["completion_condition"]) == ("Open", "explicit")
    assert detail["links"][0]["removed_at"] is not None
    assert detail["links"][0]["removed_reason"] == "daily_action_cancelled"
    assert [e["event_type"] for e in detail["events"]] == ["created", "link_removed", "condition_changed"]
    uid = await user_id_of(api, me)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        events = (
            await s.execute(
                select(m.DomainEvent.event_type).where(
                    m.DomainEvent.user_id == uid, m.DomainEvent.event_type == "commitment.condition_changed"
                )
            )
        ).scalars()
    assert len(list(events)) == 1  # the User is notified that the condition changed (M9 delivers it)


async def test_all_condition_is_satisfied_once_a_cancelled_link_is_removed(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    one, two = await manual(me), await manual(me, "Review", "16:00:00")
    body = await commit(me, links=[link(one), link(two)], completion_condition="all")
    await complete(me, one)
    assert (await get(me, body["id"]))["status"] == "Open"
    assert (await me.post(f"/daily-actions/{two}/cancel", {})).status_code == 200
    detail = await get(me, body["id"])
    assert (detail["status"], detail["completion_condition"]) == ("Kept", "all")  # one link still remains


async def test_archiving_a_goal_removes_links_of_its_cancelled_actions(api: Api) -> None:
    me = await api.as_user("ada@example.com")
    goal = (await me.post("/goals", {"title": "Health", "category": "Fitness"})).json()["data"]
    task = (
        await me.post("/tasks", {"title": "Long run", "goal_id": goal["id"], "duration_minutes": 60})
    ).json()["data"]
    action = (
        await me.post(
            f"/tasks/{task['id']}/schedule",
            {"date": "2026-09-22", "start_time": "09:00:00", "end_time": "10:00:00"},
        )
    ).json()["data"]
    body = await commit(me, links=[link(action["id"])])
    assert (await me.post(f"/goals/{goal['id']}/archive")).status_code == 200
    detail = await get(me, body["id"])
    assert (detail["status"], detail["completion_condition"]) == ("Open", "explicit")
    assert detail["links"][0]["removed_reason"] == "goal_archived"
