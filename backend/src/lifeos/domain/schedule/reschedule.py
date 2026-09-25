"""Source-aware reschedule, cancel, history, NL matching (design §12.2–12.4, §13; R2.5–2.8, R2.10).

Only Planned, active actions can be rescheduled. Every schedule change appends an immutable history row.
Source-specific effects preserve the definitions the action came from:
    ROUTINE_ENTRY → Routine Exception for the occurrence date (template untouched)
    HABIT         → Habit occurrence override (recurrence untouched)
    TASK          → Task scheduled date/time updated
    MANUAL        → the action itself only
"""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError
from lifeos.domain.schedule.common import (
    Actor,
    CancelHook,
    add_history,
    get_action,
    goal_is_archived,
    source_goal_id,
    user_timezone,
)
from lifeos.domain.timeutil import block_instants, to_local
from lifeos.events.outbox import publish

Effect = Literal["routine_exception", "habit_override", "task_update", "direct"]
MATCH_WINDOW = timedelta(minutes=15)
TITLE_THRESHOLD = 0.6


def reschedule_effect(source_type: str) -> Effect:
    """Which definition-preserving side effect a reschedule has (design §12.2)."""
    return {
        "ROUTINE_ENTRY": "routine_exception",
        "HABIT": "habit_override",
        "TASK": "task_update",
    }.get(source_type, "direct")  # type: ignore[return-value]


def ensure_reschedulable(status: str, lifecycle_state: str) -> None:
    if lifecycle_state != "active":
        raise DomainError("INVALID_TRANSITION", "A cancelled action cannot be rescheduled")
    if status != "Planned":
        raise DomainError(
            "INVALID_TRANSITION",
            f"Only planned actions can be rescheduled; this one is {status}",
            {"status": status},
        )


def ensure_cancellable(status: str, lifecycle_state: str) -> None:
    if lifecycle_state != "active":
        raise DomainError("INVALID_TRANSITION", "This action is already cancelled")
    if status not in ("Planned", "Started"):
        raise DomainError("INVALID_TRANSITION", f"A {status} action cannot be cancelled", {"status": status})


def title_similarity(a: str, b: str) -> float:
    left, right = a.casefold().strip(), b.casefold().strip()
    if left and right and (left in right or right in left):
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


@dataclass(frozen=True)
class Match:
    action: m.DailyAction
    minutes_off: float | None
    title_score: float | None


def score_candidate(
    local_start: time, title: str, match_time: time | None, match_title: str | None
) -> tuple[float | None, float | None] | None:
    """(minutes off, title score) if the candidate matches every given criterion, else None."""
    minutes_off: float | None = None
    title_score: float | None = None
    if match_time is not None:
        day = date(2000, 1, 1)
        delta = abs(datetime.combine(day, local_start) - datetime.combine(day, match_time))
        if delta > MATCH_WINDOW:
            return None
        minutes_off = delta.total_seconds() / 60
    if match_title is not None:
        title_score = title_similarity(title, match_title)
        if title_score < TITLE_THRESHOLD:
            return None
    return minutes_off, title_score


class RescheduleService:
    def __init__(self, clock: Clock, cancel_hooks: list[CancelHook] | None = None) -> None:
        self._clock = clock
        self._cancel_hooks = list(cancel_hooks or [])  # e.g. Commitment link removal (R9.9)

    async def _check_source_writable(self, s: AsyncSession, action: m.DailyAction) -> None:
        if await goal_is_archived(s, await source_goal_id(s, action)):
            raise DomainError("GOAL_ARCHIVED", "This action belongs to an archived goal and is read-only")

    async def reschedule(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        action_id: uuid.UUID,
        day: date,
        start: time,
        end: time,
        actor: Actor = "User",
        reason: str | None = None,
    ) -> m.DailyAction:
        action = await get_action(s, user_id, action_id, lock=True)
        ensure_reschedulable(action.status, action.lifecycle_state)
        await self._check_source_writable(s, action)
        tz = await user_timezone(s, user_id)
        new_start, new_end = block_instants(day, start, end, tz)
        now = self._clock.now()

        effect = reschedule_effect(action.source_type)
        if effect == "routine_exception":
            template_id = (
                await s.execute(
                    select(m.RoutineEntry.routine_template_id).where(m.RoutineEntry.id == action.source_id)
                )
            ).scalar_one()
            s.add(
                m.RoutineException(
                    routine_template_id=template_id,
                    routine_entry_id=action.source_id,
                    user_id=user_id,
                    date=action.occurrence_date,
                    exception_type="modified",
                    daily_action_id=action.id,
                    exception_payload={
                        "new_start": new_start.isoformat(),
                        "new_end": new_end.isoformat(),
                        "new_date": day.isoformat(),
                    },
                    created_at=now,
                )
            )
        elif effect == "habit_override":
            await self._upsert_habit_override(s, action, "rescheduled", new_start, new_end, reason, now)
        elif effect == "task_update":
            task = (await s.execute(select(m.Task).where(m.Task.id == action.source_id))).scalar_one()
            task.scheduled_date = day
            task.scheduled_time = start
            task.updated_at = now

        add_history(s, action, "rescheduled", new_start, new_end, actor, reason, now)
        action.scheduled_start, action.scheduled_end, action.date = new_start, new_end, day
        action.updated_at = now
        await s.flush()
        await publish(
            s,
            "daily_action.rescheduled",
            user_id,
            {"daily_action_id": str(action.id), "date": day.isoformat(), "new_start": new_start.isoformat()},
            at=now,
        )
        return action

    async def cancel(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        action_id: uuid.UUID,
        actor: Actor = "User",
        reason: str | None = None,
    ) -> m.DailyAction:
        """User cancellation: lifecycle change + a `removed` exception/override so it is never regenerated."""
        action = await get_action(s, user_id, action_id, lock=True)
        ensure_cancellable(action.status, action.lifecycle_state)
        await self._check_source_writable(s, action)
        now = self._clock.now()
        effect = reschedule_effect(action.source_type)
        if effect == "routine_exception":
            template_id = (
                await s.execute(
                    select(m.RoutineEntry.routine_template_id).where(m.RoutineEntry.id == action.source_id)
                )
            ).scalar_one()
            s.add(
                m.RoutineException(
                    routine_template_id=template_id,
                    routine_entry_id=action.source_id,
                    user_id=user_id,
                    date=action.occurrence_date,
                    exception_type="removed",
                    daily_action_id=action.id,
                    exception_payload={"reason": reason} if reason else {},
                    created_at=now,
                )
            )
        elif effect == "habit_override":
            await self._upsert_habit_override(s, action, "removed", None, None, reason, now)
        elif effect == "task_update":
            task = (await s.execute(select(m.Task).where(m.Task.id == action.source_id))).scalar_one()
            task.scheduled_date = None
            task.scheduled_time = None
            task.updated_at = now

        add_history(s, action, "cancelled", None, None, actor, reason, now)
        action.lifecycle_state = "cancelled"
        action.cancelled_at = now
        action.updated_at = now
        await s.flush()
        for hook in self._cancel_hooks:
            await hook(s, user_id, [action], actor, "daily_action_cancelled")
        await publish(
            s, "daily_action.cancelled", user_id, {"daily_action_id": str(action.id), "actor": actor}, at=now
        )
        return action

    async def _upsert_habit_override(
        self,
        s: AsyncSession,
        action: m.DailyAction,
        override_type: str,
        new_start: datetime | None,
        new_end: datetime | None,
        reason: str | None,
        now: datetime,
    ) -> None:
        values = {
            "override_type": override_type,
            "new_start": new_start,
            "new_end": new_end,
            "reason": reason,
        }
        await s.execute(
            insert(m.HabitOccurrenceOverride)
            .values(
                habit_id=action.source_id,
                user_id=action.user_id,
                occurrence_date=action.occurrence_date,
                created_at=now,
                **values,
            )
            .on_conflict_do_update(index_elements=["habit_id", "occurrence_date"], set_=values)
        )

    async def history(
        self, s: AsyncSession, user_id: uuid.UUID, action_id: uuid.UUID
    ) -> list[m.DailyActionScheduleHistory]:
        await get_action(s, user_id, action_id)
        return list(
            (
                await s.execute(
                    select(m.DailyActionScheduleHistory)
                    .where(m.DailyActionScheduleHistory.daily_action_id == action_id)
                    .order_by(m.DailyActionScheduleHistory.changed_at, m.DailyActionScheduleHistory.id)
                )
            ).scalars()
        )

    async def match_actions(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        day: date,
        match_time: time | None = None,
        match_title: str | None = None,
    ) -> list[Match]:
        """Deterministic candidate matching for "Move my 4 PM learning session…" (R2.10, §12.4)."""
        if match_time is None and match_title is None:
            raise DomainError("VALIDATION_ERROR", "Give a time, a title, or both to match")
        tz = await user_timezone(s, user_id)
        rows = (
            await s.execute(
                select(m.DailyAction).where(
                    m.DailyAction.user_id == user_id,
                    m.DailyAction.date == day,
                    m.DailyAction.lifecycle_state == "active",
                )
            )
        ).scalars()
        matches: list[Match] = []
        for action in rows:
            scored = score_candidate(
                to_local(action.scheduled_start, tz).time(), action.title, match_time, match_title
            )
            if scored is not None:
                matches.append(Match(action, *scored))
        return sorted(
            matches, key=lambda x: (x.minutes_off or 0.0, -(x.title_score or 0.0), x.action.scheduled_start)
        )
