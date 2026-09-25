"""T7.2: Memory Store API — creation, browsing, editing, superseding, deletion (R4, R18.8, R18.11)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session, user_session
from lifeos.domain.memory.service import MemoryDraft
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import user_id_of

EMAIL = "nadia@example.com"
BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def add(me: AuthedClient, content: str, **body: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": content, "type": "Value"} | body
    r = await me.post("/memory", payload)
    assert r.status_code == 201, r.text
    return dict(r.json()["data"])


async def browse(me: AuthedClient, **params: Any) -> list[dict[str, Any]]:
    r = await me.get("/memory", **params)
    assert r.status_code == 200, r.text
    return list(r.json()["data"])


async def test_user_added_entry_gets_the_user_stated_defaults(api: Api) -> None:  # R4.2, R4.6
    me = await api.as_user(EMAIL)
    entry = await add(me, "I value deep work over busywork", categories=["Values"])
    assert (entry["type"], entry["source"], entry["is_inference"]) == ("Value", "User-stated", False)
    assert (entry["importance"], entry["confidence"]) == (7, "1.0")
    assert (entry["categories"], entry["embedding_status"]) == (["Values"], "pending")
    assert entry["created_at"] == entry["updated_at"]


async def test_importance_can_be_chosen_and_is_clamped(api: Api) -> None:
    me = await api.as_user(EMAIL)
    entry = await add(me, "Family dinner on Sundays", type="Principle", importance=10)
    assert entry["importance"] == 10
    r = await me.patch(f"/memory/{entry['id']}", {"importance": 3})
    assert (r.status_code, r.json()["data"]["importance"]) == (200, 3)


async def test_ai_inferred_entries_are_flagged_and_clamped(api: Api) -> None:  # R4.3, design §23.5
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        entry = await api.container.memory.create(
            s,
            uid,
            MemoryDraft(
                content="They avoid morning workouts after late calls",
                type="Pattern",
                source="AI-inferred",
                importance=9,
                confidence=Decimal("0.99"),
            ),
            "pattern_report",
        )
        entry_id = entry.id
    body = (await me.get(f"/memory/{entry_id}")).json()["data"]
    assert (body["is_inference"], body["importance"], body["confidence"]) == (True, 7, "0.80")


async def test_browse_filters_and_keyword_search(api: Api) -> None:  # R18.11
    me = await api.as_user(EMAIL)
    await add(me, "I value deep work", categories=["Values"])
    api.clock.set(api.clock.now() + timedelta(seconds=1))
    await add(me, "Morning runs keep me steady", type="Fact", categories=["Habits"])
    api.clock.set(api.clock.now() + timedelta(seconds=1))
    await add(me, "Never commit on Fridays", type="Principle", categories=["Principles"])

    assert [e["type"] for e in await browse(me)] == ["Principle", "Fact", "Value"]  # newest first
    assert [e["content"] for e in await browse(me, type="Fact")] == ["Morning runs keep me steady"]
    assert [e["type"] for e in await browse(me, category="Values")] == ["Value"]
    assert [e["type"] for e in await browse(me, q="fridays")] == ["Principle"]  # case-insensitive
    assert await browse(me, q="nothing here") == []
    assert [e["type"] for e in await browse(me, source="User-stated")] == ["Principle", "Fact", "Value"]
    assert await browse(me, source="AI-inferred") == []


async def test_browse_paginates(api: Api) -> None:
    me = await api.as_user(EMAIL)
    for index in range(3):
        await add(me, f"Value number {index}")
        api.clock.set(api.clock.now() + timedelta(seconds=1))
    first = (await me.get("/memory", limit=2)).json()
    assert len(first["data"]) == 2
    second = (await me.get("/memory", limit=2, cursor=first["meta"]["next_cursor"])).json()
    assert len(second["data"]) == 1
    assert second["meta"]["next_cursor"] is None


async def test_only_user_stated_content_can_be_edited(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    mine = await add(me, "I work best before noon")
    r = await me.patch(f"/memory/{mine['id']}", {"content": "I work best before 11"})
    assert (r.status_code, r.json()["data"]["content"]) == (200, "I work best before 11")
    assert r.json()["data"]["embedding_status"] == "pending"  # edited text must be re-embedded

    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        inferred = await api.container.memory.create(
            s,
            uid,
            MemoryDraft(content="They dislike mornings", type="Pattern", source="AI-inferred"),
            "pattern_report",
        )
        inferred_id = inferred.id
    r = await me.patch(f"/memory/{inferred_id}", {"content": "rewritten"})
    assert (r.status_code, r.json()["error"]["details"]["field"]) == (422, "content")


async def test_delete_requires_the_confirmation_phrase(api: Api) -> None:  # R18.8
    me = await api.as_user(EMAIL)
    entry = await add(me, "Something to forget")
    r = await me.delete(f"/memory/{entry['id']}")
    assert (r.status_code, r.json()["error"]["code"]) == (400, "CONFIRMATION_REQUIRED")
    assert len(await browse(me)) == 1

    wrong = await api.client.request(
        "DELETE",
        f"/api/v1/memory/{entry['id']}",
        headers={
            "Authorization": f"Bearer {me.access_token}",
            "Idempotency-Key": "delete-wrong-phrase",
            "X-Confirm-Phrase": "please",
        },
    )
    assert wrong.status_code == 400

    ok = await api.client.request(
        "DELETE",
        f"/api/v1/memory/{entry['id']}",
        headers={
            "Authorization": f"Bearer {me.access_token}",
            "Idempotency-Key": "delete-right-phrase",
            "X-Confirm-Phrase": "DELETE",
        },
    )
    assert ok.status_code == 204
    assert await browse(me) == []
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        assert (await s.execute(select(m.MemoryStoreEntry))).scalars().all() == []  # hard delete


async def test_delete_removes_the_embedding_row_only(api: Api) -> None:
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    entry = await add(me, "Ephemeral thought")
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        await api.container.embedding_backfill.embed_pending(s)
        stored = (await s.execute(select(m.MemoryStoreEntry))).scalar_one()
        assert stored.embedding is not None
        assert stored.embedding_status == "ready"
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        await api.container.memory.delete(s, uid, stored.id)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        count = (await s.execute(select(m.MemoryStoreEntry))).scalars().all()
    assert count == []
    assert entry["id"] == str(stored.id)


async def test_superseding_keeps_the_old_entry_out_of_browse(api: Api) -> None:  # design §23.6
    me = await api.as_user(EMAIL)
    uid = await user_id_of(api, me)
    old = await add(me, "I want to run a marathon this year")
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        new = await api.container.memory.supersede(
            s,
            uid,
            uuid.UUID(old["id"]),
            MemoryDraft(
                content="I want to run a half marathon this year", type="Value", source="User-stated"
            ),
            "onboarding",
        )
        new_id = str(new.id)
    assert [e["id"] for e in await browse(me)] == [new_id]
    both = await browse(me, include_superseded=True)
    assert {e["id"] for e in both} == {new_id, old["id"]}
    assert next(e for e in both if e["id"] == old["id"])["superseded_by"] == new_id


async def test_entries_are_private_to_their_user(api: Api) -> None:
    me = await api.as_user(EMAIL)
    entry = await add(me, "Private value")
    other = await api.as_user("mallory@example.com")
    assert await browse(other) == []
    assert (await other.get(f"/memory/{entry['id']}")).status_code == 404
    assert (await other.patch(f"/memory/{entry['id']}", {"importance": 1})).status_code == 404


def test_only_memory_service_inserts_memory_entries() -> None:
    """Design §23.2: `MemoryService.create()` is the single insert point. A second writer would
    silently bypass the defaults, the inference rule and the embedding queue, so scan the source."""
    writers = {
        str(path.relative_to(BACKEND_ROOT))
        for path in (BACKEND_ROOT / "src" / "lifeos").rglob("*.py")
        if any(marker in path.read_text() for marker in ("m.MemoryStoreEntry(", "insert(m.MemoryStoreEntry)"))
    }
    assert writers == {"src/lifeos/domain/memory/service.py"}, f"unexpected memory writer: {writers}"
