"""Routine Templates and Entries (design §24.4, R2.1–2.2).

Rules: at most one live template per weekday (0=Sunday..6=Saturday); entries may cross midnight
(end < start); entry IDs are stable across edits (updates never replace the row); entries may link
to a Goal and/or a Habit owned by the same user.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.goals import get_goal


def _validate_days(days: list[int]) -> list[int]:
    if not days:
        raise DomainError("VALIDATION_ERROR", "Choose at least one day", {"field": "active_days"})
    if any(d < 0 or d > 6 for d in days) or len(set(days)) != len(days):
        raise DomainError(
            "VALIDATION_ERROR",
            "Days must be unique values 0 (Sunday) … 6 (Saturday)",
            {"field": "active_days"},
        )
    return sorted(days)


async def get_template(s: AsyncSession, user_id: uuid.UUID, template_id: uuid.UUID) -> m.RoutineTemplate:
    template = (
        await s.execute(
            select(m.RoutineTemplate).where(
                m.RoutineTemplate.id == template_id,
                m.RoutineTemplate.user_id == user_id,
                m.RoutineTemplate.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if template is None:
        raise NotFoundError("routine template")
    return template


async def get_entry(s: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> m.RoutineEntry:
    entry = (
        await s.execute(
            select(m.RoutineEntry).where(
                m.RoutineEntry.id == entry_id,
                m.RoutineEntry.user_id == user_id,
                m.RoutineEntry.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise NotFoundError("routine entry")
    return entry


class RoutineService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def _ensure_no_day_conflict(
        self, s: AsyncSession, user_id: uuid.UUID, days: list[int], exclude: uuid.UUID | None
    ) -> None:
        query = select(m.RoutineTemplate).where(
            m.RoutineTemplate.user_id == user_id, m.RoutineTemplate.deleted_at.is_(None)
        )
        if exclude is not None:
            query = query.where(m.RoutineTemplate.id != exclude)
        for other in (await s.execute(query)).scalars():
            overlap = sorted(set(other.active_days) & set(days))
            if overlap:
                raise DomainError(
                    "VALIDATION_ERROR",
                    "Another routine already applies on some of these days",
                    {"field": "active_days", "conflicting_days": overlap, "template_id": str(other.id)},
                )

    async def create_template(
        self, s: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]
    ) -> m.RoutineTemplate:
        days = _validate_days(list(data["active_days"]))
        await self._ensure_no_day_conflict(s, user_id, days, None)
        now = self._clock.now()
        template = m.RoutineTemplate(
            id=uuid.uuid4(),
            user_id=user_id,
            title=data["title"],
            active_days=days,
            created_at=now,
            updated_at=now,
        )
        s.add(template)
        await s.flush()
        return template

    async def list_templates(self, s: AsyncSession, user_id: uuid.UUID) -> list[m.RoutineTemplate]:
        return list(
            (
                await s.execute(
                    select(m.RoutineTemplate)
                    .where(m.RoutineTemplate.user_id == user_id, m.RoutineTemplate.deleted_at.is_(None))
                    .order_by(m.RoutineTemplate.created_at, m.RoutineTemplate.id)
                )
            ).scalars()
        )

    async def entries(
        self, s: AsyncSession, user_id: uuid.UUID, template_id: uuid.UUID
    ) -> list[m.RoutineEntry]:
        await get_template(s, user_id, template_id)
        return list(
            (
                await s.execute(
                    select(m.RoutineEntry)
                    .where(
                        m.RoutineEntry.routine_template_id == template_id, m.RoutineEntry.deleted_at.is_(None)
                    )
                    .order_by(m.RoutineEntry.sort_order, m.RoutineEntry.start_time, m.RoutineEntry.id)
                )
            ).scalars()
        )

    async def update_template(
        self, s: AsyncSession, user_id: uuid.UUID, template_id: uuid.UUID, changes: dict[str, Any]
    ) -> m.RoutineTemplate:
        template = await get_template(s, user_id, template_id)
        if "active_days" in changes:
            days = _validate_days(list(changes["active_days"]))
            await self._ensure_no_day_conflict(s, user_id, days, template.id)
            template.active_days = days
        if "title" in changes:
            template.title = changes["title"]
        template.updated_at = self._clock.now()
        await s.flush()
        return template

    async def delete_template(self, s: AsyncSession, user_id: uuid.UUID, template_id: uuid.UUID) -> None:
        template = await get_template(s, user_id, template_id)
        now = self._clock.now()
        await s.execute(
            update(m.RoutineEntry)
            .where(m.RoutineEntry.routine_template_id == template.id, m.RoutineEntry.deleted_at.is_(None))
            .values(deleted_at=now, updated_at=now)
        )
        template.deleted_at = now
        template.updated_at = now
        await s.flush()

    async def _check_links(self, s: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]) -> None:
        if data.get("goal_id") is not None:
            await get_goal(s, user_id, data["goal_id"])
        if data.get("habit_id") is not None:
            owned = (
                await s.execute(
                    select(m.Habit.id).where(
                        m.Habit.id == data["habit_id"],
                        m.Habit.user_id == user_id,
                        m.Habit.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if owned is None:
                raise NotFoundError("habit")

    async def create_entry(
        self, s: AsyncSession, user_id: uuid.UUID, template_id: uuid.UUID, data: dict[str, Any]
    ) -> m.RoutineEntry:
        await get_template(s, user_id, template_id)
        if data["start_time"] == data["end_time"]:
            raise DomainError("VALIDATION_ERROR", "Start and end time must differ", {"field": "end_time"})
        await self._check_links(s, user_id, data)
        now = self._clock.now()
        entry = m.RoutineEntry(
            id=uuid.uuid4(),
            routine_template_id=template_id,
            user_id=user_id,
            title=data["title"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            sort_order=data.get("sort_order", 0),
            goal_id=data.get("goal_id"),
            habit_id=data.get("habit_id"),
            created_at=now,
            updated_at=now,
        )
        s.add(entry)
        await s.flush()
        return entry

    async def update_entry(
        self, s: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID, changes: dict[str, Any]
    ) -> m.RoutineEntry:
        entry = await get_entry(s, user_id, entry_id)
        await self._check_links(s, user_id, changes)
        for key in ("title", "start_time", "end_time", "sort_order", "goal_id", "habit_id"):
            if key in changes:
                setattr(entry, key, changes[key])
        if entry.start_time == entry.end_time:
            raise DomainError("VALIDATION_ERROR", "Start and end time must differ", {"field": "end_time"})
        entry.updated_at = self._clock.now()
        await s.flush()
        return entry

    async def delete_entry(self, s: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        entry = await get_entry(s, user_id, entry_id)
        now = self._clock.now()
        entry.deleted_at = now
        entry.updated_at = now
        await s.flush()
