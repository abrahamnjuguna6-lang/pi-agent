"""Semantic search over the Memory Store (design §23.4; R4.5, R4.9, R15.10).

Cosine similarity (`1 - (embedding <=> query)`) with a configurable threshold, always scoped to the
authenticated User. Pending, failed and superseded entries are excluded — a vector that does not
match its text must never be retrieved. Per-user corpora are small, so V1 runs an exact KNN over the
User's rows; above `HNSW_ENTRY_THRESHOLD` entries the query switches the HNSW index into iterative
scan so the user filter cannot starve the result set (pgvector >= 0.8).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.errors import DomainError
from lifeos.domain.memory.embeddings import Embedder

DEFAULT_K = 8
MAX_K = 20
DEFAULT_THRESHOLD = 0.75
HNSW_ENTRY_THRESHOLD = 20_000


@dataclass(frozen=True)
class SearchHit:
    entry: m.MemoryStoreEntry
    similarity: float


def clamp_k(k: int | None) -> int:
    if k is None:
        return DEFAULT_K
    if not 1 <= k <= MAX_K:
        raise DomainError("VALIDATION_ERROR", f"k must be between 1 and {MAX_K}", {"field": "k"})
    return k


def validate_threshold(threshold: float | None) -> float:
    if threshold is None:
        return DEFAULT_THRESHOLD
    if not 0.0 <= threshold <= 1.0:
        raise DomainError("VALIDATION_ERROR", "threshold must be between 0 and 1", {"field": "threshold"})
    return threshold


class MemorySearch:
    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    async def search(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        query: str,
        *,
        k: int | None = None,
        threshold: float | None = None,
        types: Sequence[str] | None = None,
        sources: Sequence[str] | None = None,
        categories: Sequence[str] | None = None,
        created_from: date | None = None,
    ) -> list[SearchHit]:
        size, minimum = clamp_k(k), validate_threshold(threshold)
        cleaned = query.strip()
        if not cleaned:
            raise DomainError("VALIDATION_ERROR", "Search query cannot be empty", {"field": "q"})
        (vector,) = await self._embedder.embed([cleaned])

        entry = m.MemoryStoreEntry
        similarity = (1 - entry.embedding.cosine_distance(vector)).label("similarity")
        conditions = [
            entry.user_id == user_id,
            entry.embedding_status == "ready",
            entry.superseded_by.is_(None),
        ]
        if types:
            conditions.append(entry.type.in_(list(types)))
        if sources:
            conditions.append(entry.source.in_(list(sources)))
        if categories:
            conditions.append(entry.categories.overlap(list(categories)))
        if created_from is not None:
            conditions.append(entry.created_at >= created_from)

        if await self._corpus_size(s, user_id) > HNSW_ENTRY_THRESHOLD:
            await s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        rows = (
            await s.execute(
                select(entry, similarity)
                .where(*conditions, similarity >= minimum)
                .order_by(similarity.desc(), entry.created_at.desc())
                .limit(size)
            )
        ).tuples()
        return [SearchHit(row, float(score)) for row, score in rows]

    async def _corpus_size(self, s: AsyncSession, user_id: uuid.UUID) -> int:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(m.MemoryStoreEntry)
                    .where(m.MemoryStoreEntry.user_id == user_id)
                )
            ).scalar_one()
        )
