"""MemoryService: the single entry point that inserts Memory Store entries (design §10.7, §23.1–23.2).

Only the creation paths in design §10.7 may write `memory_store_entries`. Entries are inserted with
`embedding_status='pending'`; the embedding backfill (T7.1) completes them.

> M6 note: this is the minimal `create()` needed by the Commitment explanation and accountability
> reflection flows. T7.2 extends it (AI-inferred clamping of model-proposed values, browse, delete,
> supersede) without changing this signature.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock

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


@dataclass(frozen=True)
class MemoryDraft:
    content: str
    type: MemoryType
    source: MemorySource
    categories: list[str] = field(default_factory=lambda: [])
    source_ref_type: str | None = None
    source_ref_id: uuid.UUID | None = None


class MemoryService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def create(
        self, s: AsyncSession, user_id: uuid.UUID, draft: MemoryDraft, path: CreationPath
    ) -> m.MemoryStoreEntry:
        importance, confidence = DEFAULTS[draft.source]
        now = self._clock.now()
        entry = m.MemoryStoreEntry(
            id=uuid.uuid4(),
            user_id=user_id,
            content=draft.content,
            type=draft.type,
            source=draft.source,
            categories=sorted(set(draft.categories)),
            importance=importance,
            confidence=confidence,
            is_inference=draft.source == "AI-inferred",  # design §23.5
            source_ref_type=draft.source_ref_type,
            source_ref_id=draft.source_ref_id,
            created_at=now,
            updated_at=now,
        )
        s.add(entry)
        await s.flush()
        return entry
