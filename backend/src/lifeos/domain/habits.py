"""Habits: CRUD, recurrence, pauses (design §17, R1.10–1.12). Occurrences & metrics: T5.9."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.checkins import CheckinService, TransitionContext
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.goals import ensure_writable, get_goal, get_project, goal_of_project
from lifeos.domain.habit_metrics import HabitDefinition, HabitMetrics, compute_metrics
from lifeos.domain.schedule.common import CancelHook, system_cancel, user_timezone
from lifeos.domain.timeutil import local_date

RECURRENCE_TYPES = ("daily", "weekly", "custom")


async def get_habit(s: AsyncSession, user_id: uuid.UUID, habit_id: uuid.UUID) -> m.Habit:
    habit = (
        await s.execute(
            select(m.Habit).where(
                m.Habit.id == habit_id, m.Habit.user_id == user_id, m.Habit.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if habit is None:
        raise NotFoundError("habit")
    return habit


def validate_recurrence(
    recurrence_type: str, days: list[int] | None, frequency_target: int
) -> list[int] | None:
    if recurrence_type not in RECURRENCE_TYPES:
        raise DomainError("VALIDATION_ERROR", "Unknown recurrence type", {"field": "recurrence_type"})
    if frequency_target < 1:
        raise DomainError(
            "VALIDATION_ERROR", "Frequency target must be at least 1", {"field": "frequency_target"}
        )
    if recurrence_type == "daily":
        return None
    if not days or any(d < 0 or d > 6 for d in days) or len(set(days)) != len(days):
        raise DomainError(
            "VALIDATION_ERROR",
            "Weekly and custom habits need unique days 0 (Sunday) … 6 (Saturday)",
            {"field": "recurrence_days"},
        )
    if frequency_target > len(days):
        raise DomainError(
            "VALIDATION_ERROR",
            "Frequency target cannot exceed the number of scheduled days per week",
            {"field": "frequency_target"},
        )
    return sorted(days)


class HabitService:
    def __init__(self, clock: Clock, cancel_hooks: list[CancelHook] | None = None) -> None:
        self._clock = clock
        self._cancel_hooks = list(cancel_hooks or [])

    async def _check_parent(
        self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID | None, project_id: uuid.UUID | None
    ) -> None:
        if goal_id is None and project_id is None:
            raise DomainError(
                "VALIDATION_ERROR", "A habit must belong to a goal or a project", {"field": "goal_id"}
            )
        if goal_id is not None:
            ensure_writable(await get_goal(s, user_id, goal_id))
        if project_id is not None:
            ensure_writable(await goal_of_project(s, user_id, await get_project(s, user_id, project_id)))

    async def create(self, s: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]) -> m.Habit:
        await self._check_parent(s, user_id, data.get("goal_id"), data.get("project_id"))
        days = validate_recurrence(
            data["recurrence_type"], data.get("recurrence_days"), data.get("frequency_target", 1)
        )
        start, end = data.get("start_date"), data.get("end_date")
        if start is not None and end is not None and end < start:
            raise DomainError(
                "VALIDATION_ERROR", "End date must not be before start date", {"field": "end_date"}
            )
        now = self._clock.now()
        habit = m.Habit(
            id=uuid.uuid4(),
            user_id=user_id,
            goal_id=data.get("goal_id"),
            project_id=data.get("project_id"),
            title=data["title"],
            recurrence_type=data["recurrence_type"],
            recurrence_days=days,
            preferred_start=data.get("preferred_start"),
            duration_minutes=data.get("duration_minutes"),
            frequency_target=data.get("frequency_target", 1),
            start_date=start,
            end_date=end,
            created_at=now,
            updated_at=now,
        )
        s.add(habit)
        await s.flush()
        return habit

    async def get(self, s: AsyncSession, user_id: uuid.UUID, habit_id: uuid.UUID) -> m.Habit:
        return await get_habit(s, user_id, habit_id)

    async def list_habits(self, s: AsyncSession, user_id: uuid.UUID) -> list[m.Habit]:
        return list(
            (
                await s.execute(
                    select(m.Habit)
                    .where(m.Habit.user_id == user_id, m.Habit.deleted_at.is_(None))
                    .order_by(m.Habit.created_at, m.Habit.id)
                )
            ).scalars()
        )

    async def update(
        self, s: AsyncSession, user_id: uuid.UUID, habit_id: uuid.UUID, changes: dict[str, Any]
    ) -> m.Habit:
        habit = await get_habit(s, user_id, habit_id)
        await self._check_parent(s, user_id, habit.goal_id, habit.project_id)
        merged = {
            "recurrence_type": changes.get("recurrence_type", habit.recurrence_type),
            "recurrence_days": changes.get("recurrence_days", habit.recurrence_days),
            "frequency_target": changes.get("frequency_target", habit.frequency_target),
        }
        habit.recurrence_days = validate_recurrence(
            merged["recurrence_type"], merged["recurrence_days"], merged["frequency_target"]
        )
        for key in (
            "title",
            "recurrence_type",
            "preferred_start",
            "duration_minutes",
            "frequency_target",
            "start_date",
            "end_date",
            "status",
        ):
            if key in changes:
                setattr(habit, key, changes[key])
        if habit.start_date and habit.end_date and habit.end_date < habit.start_date:
            raise DomainError(
                "VALIDATION_ERROR", "End date must not be before start date", {"field": "end_date"}
            )
        habit.updated_at = self._clock.now()
        await s.flush()
        return habit

    async def delete(self, s: AsyncSession, user_id: uuid.UUID, habit_id: uuid.UUID) -> None:
        habit = await get_habit(s, user_id, habit_id)
        now = self._clock.now()
        await self._cancel_future(s, habit, from_date=None, change_type="cancelled", reason="habit_deleted")
        habit.deleted_at = now
        habit.updated_at = now
        await s.flush()

    # ------------------------------------------------------------------ occurrences & metrics (T5.9)

    async def record_occurrence(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        habit_id: uuid.UUID,
        day: date,
        result: str,
        checkins: CheckinService,
        completion_percent: int | None = None,
        note: str | None = None,
    ) -> m.HabitOccurrenceRecord:
        """Record a Habit outcome (§17.2). With a scheduled occurrence it goes through the check-in state
        machine (completed/partial → Completed, skipped → Skipped) in the same transaction."""
        habit = await get_habit(s, user_id, habit_id)
        await self._check_parent(s, user_id, habit.goal_id, habit.project_id)
        if day > local_date(self._clock.now(), await user_timezone(s, user_id)):
            raise DomainError("VALIDATION_ERROR", "You can't record a future occurrence", {"field": "date"})
        if result == "partial" and not (completion_percent and 1 <= completion_percent <= 99):
            raise DomainError(
                "VALIDATION_ERROR",
                "A partial completion needs a percentage from 1 to 99",
                {"field": "completion_percent"},
            )
        if result != "partial" and completion_percent is not None:
            raise DomainError(
                "VALIDATION_ERROR",
                "Only partial completions take a percentage",
                {"field": "completion_percent"},
            )
        action = (
            await s.execute(
                select(m.DailyAction).where(
                    m.DailyAction.source_type == "HABIT",
                    m.DailyAction.source_id == habit.id,
                    m.DailyAction.occurrence_date == day,
                    m.DailyAction.lifecycle_state == "active",
                )
            )
        ).scalar_one_or_none()
        if action is not None:
            status = "Skipped" if result == "skipped" else "Completed"
            action_note = note if result != "partial" else (note or f"partial {completion_percent}%")
            await checkins.transition(
                s,
                user_id,
                action.id,
                status,
                action_note,
                "User",
                context=TransitionContext(habit_result=result, completion_percent=completion_percent),  # type: ignore[arg-type]
            )
        else:
            if result == "skipped" and not (note and note.strip()):
                raise DomainError("SKIP_REASON_REQUIRED", "Tell us why you skipped this", {"field": "note"})
            values = {"result": result, "completion_percent": completion_percent, "note": note}
            await s.execute(
                insert(m.HabitOccurrenceRecord)
                .values(
                    habit_id=habit.id,
                    user_id=user_id,
                    occurrence_date=day,
                    created_at=self._clock.now(),
                    **values,
                )
                .on_conflict_do_update(index_elements=["habit_id", "occurrence_date"], set_=values)
            )
        await s.flush()
        return (
            await s.execute(
                select(m.HabitOccurrenceRecord).where(
                    m.HabitOccurrenceRecord.habit_id == habit.id,
                    m.HabitOccurrenceRecord.occurrence_date == day,
                )
            )
        ).scalar_one()

    async def metrics(
        self, s: AsyncSession, user_id: uuid.UUID, habit_id: uuid.UUID, as_of: date | None = None
    ) -> HabitMetrics:
        habit = await get_habit(s, user_id, habit_id)
        tz = await user_timezone(s, user_id)
        today = as_of or local_date(self._clock.now(), tz)
        pauses = [
            (p.starts_on, p.ends_on)
            for p in (
                await s.execute(select(m.HabitPausePeriod).where(m.HabitPausePeriod.habit_id == habit.id))
            ).scalars()
        ]
        records = {
            r.occurrence_date: r.result
            for r in (
                await s.execute(
                    select(m.HabitOccurrenceRecord).where(m.HabitOccurrenceRecord.habit_id == habit.id)
                )
            ).scalars()
        }
        definition = HabitDefinition(
            recurrence_type=habit.recurrence_type,
            recurrence_days=list(habit.recurrence_days) if habit.recurrence_days else None,
            frequency_target=habit.frequency_target,
            start=habit.start_date or local_date(habit.created_at, tz),
            end=habit.end_date,
            pauses=pauses,
        )
        return compute_metrics(definition, records, today)

    async def pause_periods(self, s: AsyncSession, habit_id: uuid.UUID) -> list[m.HabitPausePeriod]:
        return list(
            (
                await s.execute(
                    select(m.HabitPausePeriod)
                    .where(m.HabitPausePeriod.habit_id == habit_id)
                    .order_by(m.HabitPausePeriod.starts_on)
                )
            ).scalars()
        )

    # ------------------------------------------------------------------ pauses (R1.12, §17.4)

    async def open_pause(self, s: AsyncSession, habit_id: uuid.UUID) -> m.HabitPausePeriod | None:
        return (
            await s.execute(
                select(m.HabitPausePeriod).where(
                    m.HabitPausePeriod.habit_id == habit_id, m.HabitPausePeriod.ends_on.is_(None)
                )
            )
        ).scalar_one_or_none()

    async def pause(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        habit_id: uuid.UUID,
        starts_on: date | None = None,
        reason: str | None = None,
    ) -> m.HabitPausePeriod:
        habit = await get_habit(s, user_id, habit_id)
        if await self.open_pause(s, habit.id) is not None:
            raise DomainError("INVALID_TRANSITION", "This habit is already paused")
        today = local_date(self._clock.now(), await user_timezone(s, user_id))
        start = starts_on or today
        if start < today:
            raise DomainError("VALIDATION_ERROR", "A pause cannot start in the past", {"field": "starts_on"})
        period = m.HabitPausePeriod(habit_id=habit.id, user_id=user_id, starts_on=start, reason=reason)
        s.add(period)
        await self._cancel_future(
            s, habit, from_date=start, change_type="habit_paused", reason="habit_paused"
        )
        await s.flush()
        return period

    async def resume(self, s: AsyncSession, user_id: uuid.UUID, habit_id: uuid.UUID) -> None:
        habit = await get_habit(s, user_id, habit_id)
        period = await self.open_pause(s, habit.id)
        if period is None:
            raise DomainError("INVALID_TRANSITION", "This habit is not paused")
        today = local_date(self._clock.now(), await user_timezone(s, user_id))
        if period.starts_on >= today:
            await s.delete(period)  # the pause never took effect
        else:
            period.ends_on = today - timedelta(days=1)  # pause covers [starts_on, yesterday]; today generates
        await s.flush()

    async def _cancel_future(
        self, s: AsyncSession, habit: m.Habit, from_date: date | None, change_type: str, reason: str
    ) -> None:
        now = self._clock.now()
        query = select(m.DailyAction).where(
            m.DailyAction.user_id == habit.user_id,
            m.DailyAction.source_type == "HABIT",
            m.DailyAction.source_id == habit.id,
            m.DailyAction.lifecycle_state == "active",
            m.DailyAction.status == "Planned",
            m.DailyAction.scheduled_start > now,
        )
        if from_date is not None:
            query = query.where(m.DailyAction.date >= from_date)
        cancelled = list((await s.execute(query)).scalars())
        for action in cancelled:
            system_cancel(s, action, change_type, reason, now)  # type: ignore[arg-type]
        await s.flush()
        for hook in self._cancel_hooks:
            await hook(s, habit.user_id, cancelled, "System", reason)


# --------------------------------------------------------------------------- occurrences & metrics (T5.9)


async def habit_sync_hook(
    s: AsyncSession, action: m.DailyAction, previous: str, new: str, note: str | None, ctx: TransitionContext
) -> None:
    """Same-transaction occurrence record for HABIT actions (design §17.2)."""
    if action.source_type != "HABIT" or action.source_id is None or new not in ("Completed", "Skipped"):
        return
    if new == "Skipped":
        result, percent = "skipped", None
    elif ctx.habit_result == "partial":
        result, percent = "partial", ctx.completion_percent
    else:
        result, percent = "completed", None
    values = {"result": result, "completion_percent": percent, "note": note, "daily_action_id": action.id}
    await s.execute(
        insert(m.HabitOccurrenceRecord)
        .values(
            habit_id=action.source_id,
            user_id=action.user_id,
            occurrence_date=action.occurrence_date,
            created_at=action.updated_at,
            **values,
        )
        .on_conflict_do_update(index_elements=["habit_id", "occurrence_date"], set_=values)
    )
