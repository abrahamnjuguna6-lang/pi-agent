"""MemoryService: the single entry point that writes Memory Store entries (design §10.7, §23.1–23.6).

Only the creation paths in design §10.7 may insert into `memory_store_entries`, and they all go
through `create()` — `tests/integration/test_memory_api.py` asserts that no other module inserts into
the table. Entries are written with `embedding_status='pending'`; the backfill (T7.1) fills the vector
in. `source='AI-inferred'` always implies `is_inference` (R4.3), and model-proposed importance and
confidence are clamped to the ranges in design §23.1.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.pagination import Page, SortKey, clamp_limit, keyset_sorted, to_sorted_page

MemoryType = Literal[
    "Fact",
    "Preference",
    "Value",
    "Principle",
    "Lesson",
    "Achievement",
    "Failure",
    "Reflection",
    "Commitment",
    "Pattern",
]
MemorySource = Literal["User-stated", "AI-inferred", "System-derived"]
CreationPath = Literal[
    "agent_proposal",
    "user_request",
    "daily_reflection",
    "checkin_note",
    "accountability_reflection",
    "commitment_explanation",
    "ceo_meeting",
    "onboarding",
    "pattern_report",
    "lesson_proposal",
]

# design §23.1 defaults: (importance, confidence)
DEFAULTS: dict[str, tuple[int, Decimal]] = {
    "User-stated": (7, Decimal("1.0")),
    "System-derived": (5, Decimal("1.0")),
    "AI-inferred": (5, Decimal("0.5")),
}
# design §23.1: a model-proposed value is clamped; the User's own values use the full range.
AI_IMPORTANCE = (1, 7)
AI_CONFIDENCE = (Decimal("0.3"), Decimal("0.8"))
IMPORTANCE = (1, 10)
CONFIDENCE = (Decimal("0.0"), Decimal("1.0"))
CONTENT_MAX = 4000
# Content the User may edit afterwards (design §26.2: User-stated entries only).
EDITABLE_SOURCES = ("User-stated",)


def clamp_importance(value: int | None, source: str) -> int:
    low, high = AI_IMPORTANCE if source == "AI-inferred" else IMPORTANCE
    if value is None:
        return DEFAULTS[source][0]
    return max(low, min(high, value))


def clamp_confidence(value: Decimal | None, source: str) -> Decimal:
    low, high = AI_CONFIDENCE if source == "AI-inferred" else CONFIDENCE
    if value is None:
        return DEFAULTS[source][1]
    return max(low, min(high, Decimal(value)))


def validate_content(content: str) -> str:
    cleaned = content.strip()
    if not cleaned:
        raise DomainError("VALIDATION_ERROR", "Memory content cannot be empty", {"field": "content"})
    if len(cleaned) > CONTENT_MAX:
        raise DomainError(
            "VALIDATION_ERROR",
            f"Memory content must be at most {CONTENT_MAX} characters",
            {"field": "content"},
        )
    return cleaned


@dataclass(frozen=True)
class MemoryDraft:
    content: str
    type: MemoryType
    source: MemorySource
    categories: list[str] = field(default_factory=lambda: [])
    importance: int | None = None  # model-proposed or User-chosen; defaulted and clamped on create
    confidence: Decimal | None = None
    source_ref_type: str | None = None
    source_ref_id: uuid.UUID | None = None


class MemoryService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    # ------------------------------------------------------------------ writes

    async def create(
        self, s: AsyncSession, user_id: uuid.UUID, draft: MemoryDraft, path: CreationPath
    ) -> m.MemoryStoreEntry:
        """The only insert into `memory_store_entries`. `path` is the design §10.7 creation path that
        authorized it; the confirmation for that path happens before this call (R4.7)."""
        now = self._clock.now()
        entry = m.MemoryStoreEntry(
            id=uuid.uuid4(),
            user_id=user_id,
            content=validate_content(draft.content),
            type=draft.type,
            source=draft.source,
            categories=sorted({category for category in draft.categories if category}),
            importance=clamp_importance(draft.importance, draft.source),
            confidence=clamp_confidence(draft.confidence, draft.source),
            is_inference=draft.source == "AI-inferred",  # design §23.5, R4.3
            source_ref_type=draft.source_ref_type,
            source_ref_id=draft.source_ref_id,
            embedding_status="pending",
            created_at=now,
            updated_at=now,
        )
        s.add(entry)
        await s.flush()
        return entry

    async def create_once(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        draft: MemoryDraft,
        path: CreationPath,
    ) -> m.MemoryStoreEntry | None:
        """`create()` for at-least-once event handlers: returns None when an entry for the same source
        record already exists, so replaying an event never duplicates a memory (R4.4)."""
        if draft.source_ref_id is None:  # pragma: no cover - guarded by the callers
            raise ValueError("create_once needs source_ref_id to deduplicate")
        existing = (
            await s.execute(
                select(m.MemoryStoreEntry.id).where(
                    m.MemoryStoreEntry.user_id == user_id,
                    m.MemoryStoreEntry.source_ref_type == draft.source_ref_type,
                    m.MemoryStoreEntry.source_ref_id == draft.source_ref_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return None
        return await self.create(s, user_id, draft, path)

    async def update(
        self, s: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID, changes: dict[str, Any]
    ) -> m.MemoryStoreEntry:
        """The User may re-score any entry's importance; only their own statements can be reworded
        (design §26.2). Editing the content queues a re-embedding."""
        entry = await self.get(s, user_id, entry_id)
        if "importance" in changes:
            entry.importance = clamp_importance(int(changes["importance"]), entry.source)
        if "content" in changes:
            if entry.source not in EDITABLE_SOURCES:
                raise DomainError(
                    "VALIDATION_ERROR",
                    "Only entries you stated yourself can be edited",
                    {"field": "content", "source": entry.source},
                )
            entry.content = validate_content(changes["content"])
            entry.embedding_status = "pending"  # the stored vector no longer matches the text
        entry.updated_at = self._clock.now()
        await s.flush()
        return entry

    async def supersede(
        self, s: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID, draft: MemoryDraft, path: CreationPath
    ) -> m.MemoryStoreEntry:
        """Replace an entry without losing it (re-onboarding, design §23.6). The old row stays and
        points at the new one; search and browse skip superseded rows."""
        previous = await self.get(s, user_id, entry_id)
        replacement = await self.create(s, user_id, draft, path)
        previous.superseded_by = replacement.id
        previous.updated_at = self._clock.now()
        await s.flush()
        return replacement

    async def delete(self, s: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        """Hard delete, including the embedding, in one transaction (R18.8, design §23.6). The record
        the entry came from — a Reflection, a Check-in — is not touched."""
        entry = await self.get(s, user_id, entry_id)
        await s.delete(entry)
        await s.flush()

    # ------------------------------------------------------------------ reads

    async def get(self, s: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> m.MemoryStoreEntry:
        entry = (
            await s.execute(
                select(m.MemoryStoreEntry).where(
                    m.MemoryStoreEntry.id == entry_id, m.MemoryStoreEntry.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if entry is None:
            raise NotFoundError("memory entry")
        return entry

    async def browse(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        *,
        type: str | None = None,
        source: str | None = None,
        category: str | None = None,
        q: str | None = None,
        created_from: date | None = None,
        include_superseded: bool = False,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> Page[m.MemoryStoreEntry]:
        """Memory Browser (R18.11): newest first, filterable by type, source and category, with
        keyword search over the content (trigram index)."""
        size = clamp_limit(limit)
        entry = m.MemoryStoreEntry
        query = select(entry).where(entry.user_id == user_id)
        if type is not None:
            query = query.where(entry.type == type)
        if source is not None:
            query = query.where(entry.source == source)
        if category is not None:
            query = query.where(entry.categories.contains([category]))
        if q is not None:
            query = query.where(entry.content.ilike(f"%{q}%"))
        if created_from is not None:
            query = query.where(entry.created_at >= created_from)
        if not include_superseded:
            query = query.where(entry.superseded_by.is_(None))
        key = SortKey(entry.created_at, lambda row: row.created_at.isoformat(), datetime.fromisoformat)
        rows = list((await s.execute(keyset_sorted(query, key, entry.id, cursor, size, True))).scalars())
        return to_sorted_page(rows, size, key)
