"""Memory embeddings and the backfill pass (design §23.3, §34.1; R4.4).

Entries are written with `embedding_status='pending'`; the `embedding_backfill` sweeper (T10.2) calls
`EmbeddingBackfill.embed_pending()` about once a minute to fill them in. Search only reads `ready`
rows, so a slow or failing provider degrades recall instead of blocking writes. A failed batch stays
`failed` and is picked up by the next pass.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.db import models as m
from lifeos.domain.clock import Clock

log = logging.getLogger(__name__)

DEFAULT_BATCH = 100
RETRYABLE_STATUSES = ("pending", "failed")


class Embedder(Protocol):
    """Embedding provider. The dimension is fixed per deployment (design §23.3)."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """`langchain-openai` embeddings, built lazily so importing the container needs no API key."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: object | None = None

    @property
    def model(self) -> str:
        return self._settings.embedding_model

    @property
    def dimensions(self) -> int:
        return self._settings.embedding_dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        from langchain_openai import OpenAIEmbeddings

        if self._client is None:
            key = self._settings.openai_api_key
            self._client = OpenAIEmbeddings(
                model=self.model,
                dimensions=self.dimensions,
                api_key=key,
                timeout=self._settings.embedding_timeout_s,
            )
        client: OpenAIEmbeddings = self._client  # type: ignore[assignment]
        return await client.aembed_documents(list(texts))


class FakeEmbedder:
    """Deterministic test double: the same text always gives the same unit vector, and different
    texts give near-orthogonal ones. `overrides` pins exact vectors for similarity assertions, and
    `fail_on` makes a text raise so the failure path can be exercised."""

    def __init__(
        self,
        dimensions: int = 1536,
        model: str = "fake-embedding",
        overrides: dict[str, list[float]] | None = None,
        fail_on: Sequence[str] = (),
    ) -> None:
        self._dimensions = dimensions
        self._model = model
        self.overrides = dict(overrides or {})
        self.fail_on = list(fail_on)
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        for text in texts:
            if any(marker in text for marker in self.fail_on):
                raise RuntimeError(f"embedding provider failed for: {text[:40]}")
        return [self.overrides.get(text) or fake_vector(text, self._dimensions) for text in texts]


def fake_vector(text: str, dimensions: int) -> list[float]:
    """A unit vector derived from the text alone — stable across processes and runs."""
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")
    rng = random.Random(seed)  # noqa: S311 - a deterministic test fixture, never a secret
    raw = [rng.gauss(0.0, 1.0) for _ in range(dimensions)]
    norm = math.sqrt(sum(value * value for value in raw)) or 1.0
    return [value / norm for value in raw]


def unit_vector(axis: int, dimensions: int) -> list[float]:
    """Basis vector: cosine similarity 1 with itself and 0 with any other axis (test fixtures)."""
    vector = [0.0] * dimensions
    vector[axis] = 1.0
    return vector


@dataclass(frozen=True)
class BackfillResult:
    ready: int = 0
    failed: int = 0


class EmbeddingBackfill:
    def __init__(self, clock: Clock, embedder: Embedder) -> None:
        self._clock = clock
        self._embedder = embedder

    async def embed_pending(self, s: AsyncSession, limit: int = DEFAULT_BATCH) -> BackfillResult:
        """Embed one batch of pending (or previously failed) entries. Runs in a system session, so it
        spans users; rows are locked with SKIP LOCKED so several workers can share the queue."""
        rows = list(
            (
                await s.execute(
                    select(m.MemoryStoreEntry)
                    .where(m.MemoryStoreEntry.embedding_status.in_(RETRYABLE_STATUSES))
                    .order_by(m.MemoryStoreEntry.created_at, m.MemoryStoreEntry.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        if not rows:
            return BackfillResult()
        now = self._clock.now()
        try:
            vectors = await self._embedder.embed([row.content for row in rows])
        except Exception:  # provider error: retried by the next pass (design §23.3)
            log.warning("embedding batch failed for %d entries", len(rows), exc_info=True)
            for row in rows:
                row.embedding_status = "failed"
                row.updated_at = now
            await s.flush()
            return BackfillResult(failed=len(rows))
        for row, vector in zip(rows, vectors, strict=True):
            row.embedding = vector
            row.embedding_model = self._embedder.model
            row.embedding_status = "ready"
            row.updated_at = now
        await s.flush()
        return BackfillResult(ready=len(rows))
