"""Proactive flags: deterministic "raise this in the next session" markers (design §18.1).

Owning services create flags here; `dedupe_key` makes creation idempotent per trigger (for example one
`integrity_below:{crossing_date}` flag per crossing). Session openers and surfacing (`ProactiveService`)
build on this in T13.1.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m

FlagType = Literal[
    "escalation_level_3plus",
    "integrity_below_threshold",
    "ceo_skipped_consecutive",
    "lesson_proposal",
    "schedule_suggestion",
]
OwnerAgent = Literal["personal_assistant", "mentor", "accountability"]


async def raise_flag(
    s: AsyncSession,
    user_id: uuid.UUID,
    flag_type: FlagType,
    owner: OwnerAgent,
    dedupe_key: str,
    at: datetime,
    *,
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Create the flag unless one with the same dedupe key exists. Returns True if created."""
    created = (
        await s.execute(
            insert(m.ProactiveFlag)
            .values(
                user_id=user_id,
                flag_type=flag_type,
                owner_agent=owner,
                dedupe_key=dedupe_key,
                ref_type=ref_type,
                ref_id=ref_id,
                payload=payload or {},
                created_at=at,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
            .returning(m.ProactiveFlag.id)
        )
    ).scalar_one_or_none()
    return created is not None


async def resolve_flags(
    s: AsyncSession,
    user_id: uuid.UUID,
    flag_type: FlagType,
    at: datetime,
    *,
    ref_id: uuid.UUID | None = None,
) -> None:
    """Resolve the open flags of a type (optionally for one referenced record)."""
    query = update(m.ProactiveFlag).where(
        m.ProactiveFlag.user_id == user_id,
        m.ProactiveFlag.flag_type == flag_type,
        m.ProactiveFlag.resolved_at.is_(None),
    )
    if ref_id is not None:
        query = query.where(m.ProactiveFlag.ref_id == ref_id)
    await s.execute(query.values(resolved_at=at))
