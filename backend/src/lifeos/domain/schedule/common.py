"""Shared scheduling helpers: user timezone, schedule history, cancellation, source → goal lineage."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.errors import NotFoundError

Actor = Literal["User", "Agent", "System"]
ChangeType = Literal["rescheduled", "cancelled", "timezone_change", "habit_paused"]


async def user_timezone(s: AsyncSession, user_id: uuid.UUID) -> str:
    tz = (await s.execute(select(m.User.timezone).where(m.User.id == user_id))).scalar_one_or_none()
    if tz is None:
        raise NotFoundError("user")
    return tz


async def get_action(
    s: AsyncSession, user_id: uuid.UUID, action_id: uuid.UUID, *, lock: bool = False
) -> m.DailyAction:
    query = select(m.DailyAction).where(m.DailyAction.id == action_id, m.DailyAction.user_id == user_id)
    if lock:
        query = query.with_for_update()
    action = (await s.execute(query)).scalar_one_or_none()
    if action is None:
        raise NotFoundError("daily action")
    return action


def add_history(
    s: AsyncSession,
    action: m.DailyAction,
    change_type: ChangeType,
    new_start: datetime | None,
    new_end: datetime | None,
    actor: Actor,
    reason: str | None,
    at: datetime,
) -> None:
    """Append-only schedule log (design §13): previous times come from the action BEFORE the change."""
    s.add(
        m.DailyActionScheduleHistory(
            daily_action_id=action.id,
            user_id=action.user_id,
            change_type=change_type,
            previous_start=action.scheduled_start,
            previous_end=action.scheduled_end,
            new_start=new_start,
            new_end=new_end,
            changed_by=actor,
            reason=reason,
            changed_at=at,
        )
    )


def system_cancel(
    s: AsyncSession, action: m.DailyAction, change_type: ChangeType, reason: str, at: datetime
) -> None:
    """Lifecycle cancel (not a status) by the System, with a history row. No exception/override is
    written, so the generator may create the occurrence again later (design §12.1)."""
    add_history(s, action, change_type, None, None, "System", reason, at)
    action.lifecycle_state = "cancelled"
    action.cancelled_at = at
    action.updated_at = at


async def source_goal_id(s: AsyncSession, action: m.DailyAction) -> uuid.UUID | None:
    """Goal owning the action's source (for the archived-goal read-only rule, R1.14)."""
    if action.source_id is None:
        return None
    if action.source_type == "TASK":
        task = (await s.execute(select(m.Task).where(m.Task.id == action.source_id))).scalar_one_or_none()
        if task is None:
            return None
        if task.goal_id is not None:
            return task.goal_id
        return await _goal_of_project(s, task.project_id)
    if action.source_type == "HABIT":
        habit = (await s.execute(select(m.Habit).where(m.Habit.id == action.source_id))).scalar_one_or_none()
        if habit is None:
            return None
        if habit.goal_id is not None:
            return habit.goal_id
        return await _goal_of_project(s, habit.project_id)
    entry_goal = (
        await s.execute(select(m.RoutineEntry.goal_id).where(m.RoutineEntry.id == action.source_id))
    ).scalar_one_or_none()
    return entry_goal


async def _goal_of_project(s: AsyncSession, project_id: uuid.UUID | None) -> uuid.UUID | None:
    if project_id is None:
        return None
    return (
        await s.execute(
            select(m.Objective.goal_id)
            .join(m.Project, m.Project.objective_id == m.Objective.id)
            .where(m.Project.id == project_id)
        )
    ).scalar_one_or_none()


async def goal_is_archived(s: AsyncSession, goal_id: uuid.UUID | None) -> bool:
    if goal_id is None:
        return False
    status = (await s.execute(select(m.Goal.status).where(m.Goal.id == goal_id))).scalar_one_or_none()
    return status == "archived"
