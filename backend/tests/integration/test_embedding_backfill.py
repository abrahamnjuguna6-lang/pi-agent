"""T7.1: the embedding backfill — pending → ready, and a failed batch retried on the next pass."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session
from lifeos.domain.memory.embeddings import EmbeddingBackfill, FakeEmbedder, fake_vector
from tests.integration.api_harness import Api, AuthedClient

EMAIL = "pia@example.com"


async def add(me: AuthedClient, content: str) -> dict[str, Any]:
    r = await me.post("/memory", {"content": content, "type": "Value"})
    assert r.status_code == 201, r.text
    return dict(r.json()["data"])


async def entries(api: Api) -> list[m.MemoryStoreEntry]:
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        return list(
            (
                await s.execute(
                    select(m.MemoryStoreEntry).order_by(m.MemoryStoreEntry.created_at, m.MemoryStoreEntry.id)
                )
            ).scalars()
        )


async def run_backfill(api: Api, backfill: EmbeddingBackfill | None = None, limit: int = 100) -> Any:
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        return await (backfill or api.container.embedding_backfill).embed_pending(s, limit)


async def test_pending_entries_become_ready(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await add(me, "I value deep work")
    api.clock.set(api.clock.now() + timedelta(seconds=1))  # rows created at one instant tie on order
    await add(me, "Runs clear my head")
    assert [e.embedding_status for e in await entries(api)] == ["pending", "pending"]

    result = await run_backfill(api)
    assert (result.ready, result.failed) == (2, 0)
    stored = await entries(api)
    assert [e.embedding_status for e in stored] == ["ready", "ready"]
    assert [e.embedding_model for e in stored] == ["fake-embedding", "fake-embedding"]
    # pgvector stores float4, so the round trip is single precision
    assert stored[0].embedding is not None
    assert list(stored[0].embedding) == pytest.approx(
        fake_vector("I value deep work", api.embedder.dimensions), rel=1e-6
    )

    assert (await run_backfill(api)).ready == 0  # nothing left to do


async def test_a_failed_batch_is_retried_on_the_next_pass(api: Api) -> None:
    me = await api.as_user(EMAIL)
    await add(me, "This one trips the provider: boom")
    failing = EmbeddingBackfill(api.clock, FakeEmbedder(api.embedder.dimensions, fail_on=["boom"]))

    result = await run_backfill(api, failing)
    assert (result.ready, result.failed) == (0, 1)
    assert [e.embedding_status for e in await entries(api)] == ["failed"]
    assert [e.embedding for e in await entries(api)] == [None]

    recovered = await run_backfill(api)  # the provider is healthy again
    assert (recovered.ready, recovered.failed) == (1, 0)
    assert [e.embedding_status for e in await entries(api)] == ["ready"]


async def test_the_batch_size_is_respected(api: Api) -> None:
    me = await api.as_user(EMAIL)
    for index in range(3):
        await add(me, f"Entry {index}")
        api.clock.set(api.clock.now() + timedelta(seconds=1))  # the queue is ordered by created_at
    assert (await run_backfill(api, limit=2)).ready == 2
    assert [e.embedding_status for e in await entries(api)] == ["ready", "ready", "pending"]
    assert (await run_backfill(api, limit=2)).ready == 1


async def test_edited_content_is_re_embedded(api: Api) -> None:
    me = await api.as_user(EMAIL)
    entry = await add(me, "I value deep work")
    await run_backfill(api)
    before = (await entries(api))[0]
    assert before.embedding_status == "ready"

    r = await me.patch(f"/memory/{entry['id']}", {"content": "I value uninterrupted mornings"})
    assert r.status_code == 200, r.text
    assert (await entries(api))[0].embedding_status == "pending"
    await run_backfill(api)
    after = (await entries(api))[0]
    assert after.embedding_status == "ready"
    assert after.embedding is not None
    assert list(after.embedding) == pytest.approx(
        fake_vector("I value uninterrupted mornings", api.embedder.dimensions), rel=1e-6
    )
