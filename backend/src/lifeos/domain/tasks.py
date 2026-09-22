"""Tasks: CRUD with optional Project / Goal links (R1.9, R3.7). Scheduling & completion arrive in M5."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.goals import ensure_writable, get_goal, get_project, goal_of_project
from lifeos.domain.pagination import Page, clamp_limit, keyset, to_page

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

    async def delete(self, s: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
        task = await get_task(s, user_id, task_id)
        await self._check_links(s, user_id, task.project_id, task.goal_id)
        now = self._clock.now()
        task.deleted_at = now
        task.updated_at = now
        await s.flush()
