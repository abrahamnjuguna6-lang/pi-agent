"""Tasks: CRUD with optional Project / Goal links (R1.9, R3.7). Scheduling & completion arrive in M5."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.goals import ensure_writable, get_goal, get_project, goal_of_project
from lifeos.domain.pagination import Page, clamp_limit, keyset, to_page
from lifeos.domain.schedule.actions import insert_action_with_initial_checkin
from lifeos.domain.schedule.common import user_timezone
from lifeos.domain.timeutil import block_instants

if TYPE_CHECKING:
    from lifeos.domain.checkins import CheckinService, TransitionSource

TASK_STATUSES = ("open", "in_progress", "completed", "cancelled")


async def get_task(s: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> m.Task:
    task = (
        await s.execute(
            select(m.Task).where(m.Task.id == task_id, m.Task.user_id == user_id, m.Task.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError("task")
    return task


class TaskService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def _check_links(
        self, s: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID | None, goal_id: uuid.UUID | None
    ) -> None:
        """Linked Project/Goal must exist, be owned, and not be archived (R1.14)."""
        if project_id is not None:
            ensure_writable(await goal_of_project(s, user_id, await get_project(s, user_id, project_id)))
        if goal_id is not None:
            ensure_writable(await get_goal(s, user_id, goal_id))

    async def create(self, s: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]) -> m.Task:
        await self._check_links(s, user_id, data.get("project_id"), data.get("goal_id"))
        now = self._clock.now()
        task = m.Task(
            id=uuid.uuid4(),
            user_id=user_id,
            project_id=data.get("project_id"),
            goal_id=data.get("goal_id"),
            title=data["title"],
            due_date=data.get("due_date"),
            duration_minutes=data.get("duration_minutes"),
            created_at=now,
            updated_at=now,
        )
        s.add(task)
        await s.flush()
        return task

    async def get(self, s: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> m.Task:
        return await get_task(s, user_id, task_id)

    async def list_tasks(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        *,
        status: str | None = None,
        due_before: date | None = None,
        project_id: uuid.UUID | None = None,
        goal_id: uuid.UUID | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> Page[m.Task]:
        size = clamp_limit(limit)
        query = select(m.Task).where(m.Task.user_id == user_id, m.Task.deleted_at.is_(None))
        if status is not None:
            query = query.where(m.Task.status == status)
        if due_before is not None:
            query = query.where(m.Task.due_date < due_before)
        if project_id is not None:
            query = query.where(m.Task.project_id == project_id)
        if goal_id is not None:
            query = query.where(m.Task.goal_id == goal_id)
        rows = list((await s.execute(keyset(query, m.Task.created_at, m.Task.id, cursor, size))).scalars())
        return to_page(rows, size)

    async def update(
        self, s: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, changes: dict[str, Any]
    ) -> m.Task:
        task = await get_task(s, user_id, task_id)
        await self._check_links(s, user_id, task.project_id, task.goal_id)  # current parents writable
        await self._check_links(s, user_id, changes.get("project_id"), changes.get("goal_id"))  # new parents
        if "status" in changes and changes["status"] not in TASK_STATUSES:
            raise DomainError("VALIDATION_ERROR", "Unknown task status", {"field": "status"})
        for key in ("title", "due_date", "duration_minutes", "project_id", "goal_id", "status"):
            if key in changes:
                setattr(task, key, changes[key])
        if changes.get("status") == "completed" and task.completed_at is None:
            task.completed_at = self._clock.now()
        task.updated_at = self._clock.now()
        await s.flush()
        return task

    async def schedule(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        day: date,
        start: time,
        end: time | None,
    ) -> m.DailyAction:
        """R1.9: scheduling a Task on a date and time produces a TASK-sourced Daily Action."""
        task = await get_task(s, user_id, task_id)
        await self._check_links(s, user_id, task.project_id, task.goal_id)
        if task.status in ("completed", "cancelled"):
            raise DomainError("INVALID_TRANSITION", f"A {task.status} task cannot be scheduled")
        if await active_task_action(s, task.id) is not None:
            raise DomainError(
                "INVALID_TRANSITION", "This task is already scheduled; reschedule the existing action instead"
            )
        tz = await user_timezone(s, user_id)
        finish = end or (datetime.combine(day, start) + timedelta(minutes=task.duration_minutes or 30)).time()
        begin, stop = block_instants(day, start, finish, tz)
        now = self._clock.now()
        action = m.DailyAction(
            id=uuid.uuid4(),
            user_id=user_id,
            title=task.title,
            date=day,
            occurrence_date=day,
            scheduled_start=begin,
            scheduled_end=stop,
            status="Planned",
            source_type="TASK",
            source_id=task.id,
            created_at=now,
            updated_at=now,
        )
        task.scheduled_date = day
        task.scheduled_time = start
        task.updated_at = now
        return await insert_action_with_initial_checkin(s, action, self._clock)

    async def complete(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        task_id: uuid.UUID,
        checkins: CheckinService,
        source: TransitionSource = "User",
        note: str | None = None,
    ) -> tuple[m.Task, m.DailyAction | None]:
        """R3.10 `complete_task(target_type=task)`: completes the Task and, in the SAME transaction, its
        currently scheduled Planned/Started Daily Action (via the check-in state machine)."""
        task = await get_task(s, user_id, task_id)
        await self._check_links(s, user_id, task.project_id, task.goal_id)
        if task.status in ("completed", "cancelled"):
            raise DomainError("INVALID_TRANSITION", f"This task is already {task.status}")
        action = await active_task_action(s, task.id)
        if action is not None:
            await checkins.transition(
                s, user_id, action.id, "Completed", note, source
            )  # hook completes the task
        else:
            now = self._clock.now()
            task.status = "completed"
            task.completed_at = now
            task.updated_at = now
        await s.flush()
        return task, action

    async def delete(self, s: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
        task = await get_task(s, user_id, task_id)
        await self._check_links(s, user_id, task.project_id, task.goal_id)
        now = self._clock.now()
        task.deleted_at = now
        task.updated_at = now
        await s.flush()


# --------------------------------------------------------------------------- scheduling & sync (T5.5)


async def task_sync_hook(
    s: AsyncSession, action: m.DailyAction, previous: str, new: str, note: str | None, ctx: object
) -> None:
    """Same-transaction Task ↔ Daily Action sync (§16.2): Started → in_progress, Completed → completed."""
    if action.source_type != "TASK" or action.source_id is None:
        return
    task = (await s.execute(select(m.Task).where(m.Task.id == action.source_id))).scalar_one_or_none()
    if task is None:
        return
    if new == "Started" and task.status == "open":
        task.status = "in_progress"
        task.updated_at = action.updated_at
    elif new == "Completed" and task.status != "completed":
        task.status = "completed"
        task.completed_at = action.completed_at
        task.updated_at = action.updated_at


async def active_task_action(s: AsyncSession, task_id: uuid.UUID) -> m.DailyAction | None:
    return (
        await s.execute(
            select(m.DailyAction)
            .where(
                m.DailyAction.source_type == "TASK",
                m.DailyAction.source_id == task_id,
                m.DailyAction.lifecycle_state == "active",
                m.DailyAction.status.in_(["Planned", "Started"]),
            )
            .order_by(m.DailyAction.scheduled_start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
