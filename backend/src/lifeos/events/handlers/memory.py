"""Automatic Memory creation paths (design §10.7, §23.3; R4.4, R11.5).

Two of the §10.7 paths are driven by events rather than by a request: a Check-in note or skip reason
becomes a Fact, and a submitted Reflection becomes a Reflection entry. The User's own submission is
the authorizing action, so no confirmation card is involved; conversation history is still never
promoted automatically (R4.7).

Handlers run from the outbox, which is at-least-once, so every write goes through
`MemoryService.create_once()` keyed on the source record. Check-in notes are stored together with the
Daily Action title and local date, because the note alone ("too tired") embeds into nothing useful
(design §23.3).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.lineage import goal_category
from lifeos.domain.memory.service import MemoryDraft, MemoryService, MemorySource
from lifeos.domain.schedule.common import goal_of_source

# The accountability reflection writes its own entry in the submitting transaction (design §19.8),
# so the handler must not write a second one for it.
INLINE_REFLECTION_TYPES = ("accountability",)
REFLECTION_SOURCES: dict[str, MemorySource] = {"daily": "User-stated", "weekly_ceo": "System-derived"}
REFLECTION_CATEGORY = "Reflections"


class MemoryEventHandlers:
    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory

    async def on_checkin(self, s: AsyncSession, event: m.DomainEvent) -> None:
        """`daily_action.status_changed` → a Fact for the note or skip reason (R4.4)."""
        payload = event.payload
        note = (payload.get("note") or "").strip()
        if not note:
            return
        source_id = payload.get("source_id")
        goal_id = await goal_of_source(s, payload["source_type"], uuid.UUID(source_id) if source_id else None)
        category = await goal_category(s, goal_id)
        await self._memory.create_once(
            s,
            event.user_id,
            MemoryDraft(
                content=f"{payload['title']} on {payload['date']} — {payload['new_status'].lower()}: {note}",
                type="Fact",
                source="User-stated",
                categories=[category] if category else [],
                source_ref_type="checkin",
                source_ref_id=uuid.UUID(payload["checkin_id"]),
            ),
            "checkin_note",
        )

    async def on_reflection(self, s: AsyncSession, event: m.DomainEvent) -> None:
        """`reflection.submitted` → a Reflection entry (R11.5; the daily flow arrives in T13.5)."""
        payload = event.payload
        if payload.get("type") in INLINE_REFLECTION_TYPES:
            return
        reflection_id = uuid.UUID(payload["reflection_id"])
        reflection = (
            await s.execute(
                select(m.Reflection).where(
                    m.Reflection.id == reflection_id, m.Reflection.user_id == event.user_id
                )
            )
        ).scalar_one_or_none()
        if reflection is None:  # deleted before the event was processed
            return
        await self._memory.create_once(
            s,
            event.user_id,
            MemoryDraft(
                content=reflection.content,
                type="Reflection",
                source=REFLECTION_SOURCES.get(reflection.type, "User-stated"),
                categories=[REFLECTION_CATEGORY, *reflection.goal_categories],
                source_ref_type="reflection",
                source_ref_id=reflection.id,
            ),
            "daily_reflection",
        )
