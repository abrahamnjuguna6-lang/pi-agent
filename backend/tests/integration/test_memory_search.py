"""T7.3: semantic search — ordering, threshold, isolation and exclusions (design §23.4; R4.5, R4.9, R15.10).

Vectors are pinned through the fake embedder, so the cosine similarities are exact: an entry embedded
at `mix(axis, 0.9)` sits at 0.9 similarity to the query axis, which makes threshold behaviour testable
without depending on a real model.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from lifeos.db.session import system_session, user_session
from lifeos.domain.memory.embeddings import unit_vector
from lifeos.domain.memory.service import MemoryDraft
from tests.integration.api_harness import Api, AuthedClient
from tests.integration.sched_helpers import user_id_of

EMAIL = "sasha@example.com"
QUERY = "what do I believe about focus?"


def mix(axis: int, similarity: float, dimensions: int) -> list[float]:
    """A unit vector whose cosine similarity with `unit_vector(axis)` is exactly `similarity`."""
    vector = [0.0] * dimensions
    vector[axis] = similarity
    vector[(axis + 1) % dimensions] = math.sqrt(1 - similarity**2)
    return vector


async def seed(api: Api, me: AuthedClient, entries: list[tuple[str, float]], **draft: Any) -> None:
    """Create entries whose embeddings sit at the given similarity to the query axis."""
    dimensions = api.embedder.dimensions
    api.embedder.overrides[QUERY] = unit_vector(0, dimensions)
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        for content, similarity in entries:
            api.embedder.overrides[content] = mix(0, similarity, dimensions)
            await api.container.memory.create(
                s,
                uid,
                MemoryDraft(content=content, type=draft.get("type", "Value"), source="User-stated"),
                "user_request",
            )
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        await api.container.embedding_backfill.embed_pending(s)


async def search(me: AuthedClient, **params: Any) -> list[dict[str, Any]]:
    r = await me.get("/memory/search", q=QUERY, **params)
    assert r.status_code == 200, r.text
    return list(r.json()["data"])


async def test_results_are_ordered_by_similarity_descending(api: Api) -> None:  # R15.10
    me = await api.as_user(EMAIL)
    await seed(api, me, [("middling", 0.85), ("closest", 0.99), ("weaker", 0.78)])
    hits = await search(me)
    assert [h["content"] for h in hits] == ["closest", "middling", "weaker"]
    assert [round(h["similarity"], 2) for h in hits] == [0.99, 0.85, 0.78]


async def test_the_threshold_filters_weak_matches(api: Api) -> None:  # R4.9
    me = await api.as_user(EMAIL)
    await seed(api, me, [("strong", 0.95), ("borderline", 0.74)])
    assert [h["content"] for h in await search(me)] == ["strong"]  # default threshold 0.75
    assert [h["content"] for h in await search(me, threshold=0.7)] == ["strong", "borderline"]
    assert await search(me, threshold=0.99) == []


async def test_k_limits_the_result_count(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await seed(api, me, [(f"entry {index}", 0.99 - index / 100) for index in range(5)])
    assert len(await search(me, k=2)) == 2
    assert [h["content"] for h in await search(me, k=2)] == ["entry 0", "entry 1"]
    r = await me.get("/memory/search", q=QUERY, k=50)
    assert (r.status_code, r.json()["error"]["details"]["field"]) == (422, "k")


async def test_pending_and_failed_entries_are_never_returned(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await seed(api, me, [("embedded", 0.95)])
    uid = await user_id_of(api, me)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        draft = MemoryDraft(content="not embedded yet", type="Value", source="User-stated")
        await api.container.memory.create(s, uid, draft, "user_request")
    api.embedder.overrides["not embedded yet"] = mix(0, 1.0, api.embedder.dimensions)
    assert [h["content"] for h in await search(me)] == ["embedded"]  # still pending → not searchable


async def test_superseded_entries_are_excluded(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await seed(api, me, [("the old belief", 0.99)])
    uid = await user_id_of(api, me)
    old_id = uuid.UUID((await search(me))[0]["id"])
    api.embedder.overrides["the new belief"] = mix(0, 0.9, api.embedder.dimensions)
    async with user_session(uid, sessionmaker=api.container.sessionmaker) as s:
        await api.container.memory.supersede(
            s,
            uid,
            old_id,
            MemoryDraft(content="the new belief", type="Value", source="User-stated"),
            "onboarding",
        )
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        await api.container.embedding_backfill.embed_pending(s)
    assert [h["content"] for h in await search(me)] == ["the new belief"]


async def test_results_are_scoped_to_the_user(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await seed(api, me, [("my private value", 0.99)])
    other = await api.as_user("mallory@example.com")
    assert await search(other) == []
    assert [h["content"] for h in await search(me)] == ["my private value"]


async def test_type_filter(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await seed(api, me, [("a value I hold", 0.95)], type="Value")
    await seed(api, me, [("a fact about me", 0.96)], type="Fact")
    assert [h["content"] for h in await search(me, type="Fact")] == ["a fact about me"]
    assert len(await search(me)) == 2


async def test_empty_query_is_rejected(api: Api) -> None:
    me = await api.as_user(EMAIL)
    r = await me.get("/memory/search", q="   ")
    assert (r.status_code, r.json()["error"]["details"]["field"]) == (422, "q")
