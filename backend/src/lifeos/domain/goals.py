"""Goals and Projects: CRUD, priority ordering, archive, cascade delete, hierarchy (design §15, §24.3; R1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.schedule.common import CancelHook, system_cancel

GoalCategory = Literal["Life", "Career", "Personal", "Spiritual", "Fitness", "Family"]
GoalStatus = Literal["active", "archived", "completed"]


# --------------------------------------------------------------------------- deterministic sort (§15.2)


def priority_sort_key(goal: m.Goal) -> tuple[int, int, date, datetime, str]:
    """priority DESC, dated before undated, target_date ASC, created_at ASC, id ASC."""
    return (
        -goal.priority,
        0 if goal.target_date is not None else 1,
        goal.target_date or date.max,
        goal.created_at,
        str(goal.id),
    )


def sort_by_priority(goals: list[m.Goal]) -> list[m.Goal]:
    return sorted(goals, key=priority_sort_key)


# --------------------------------------------------------------------------- guards


async def get_goal(s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID) -> m.Goal:
    goal = (
        await s.execute(
            select(m.Goal).where(m.Goal.id == goal_id, m.Goal.user_id == user_id, m.Goal.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if goal is None:
        raise NotFoundError("goal")
    return goal


def ensure_writable(goal: m.Goal) -> None:
    """R1.14: an archived Goal's subtree is read-only."""
    if goal.status == "archived":
        raise DomainError(
            "GOAL_ARCHIVED", "This goal is archived; its items are read-only", {"goal_id": str(goal.id)}
        )


async def get_project(s: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID) -> m.Project:
    project = (
        await s.execute(
            select(m.Project).where(
                m.Project.id == project_id, m.Project.user_id == user_id, m.Project.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("project")
    return project


async def goal_of_objective(s: AsyncSession, user_id: uuid.UUID, objective_id: uuid.UUID) -> m.Goal:
    goal_id = (
        await s.execute(
            select(m.Objective.goal_id).where(
                m.Objective.id == objective_id,
                m.Objective.user_id == user_id,
                m.Objective.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if goal_id is None:
        raise NotFoundError("objective")
    return await get_goal(s, user_id, goal_id)


async def goal_of_project(s: AsyncSession, user_id: uuid.UUID, project: m.Project) -> m.Goal:
    return await goal_of_objective(s, user_id, project.objective_id)


# --------------------------------------------------------------------------- cascade description


@dataclass(frozen=True)
class CascadeCounts:
    objectives: int
    active_objectives: int
    projects: int
    tasks: int
    habits: int

    def as_dict(self) -> dict[str, int]:
        return {
            "objectives": self.objectives,
            "active_objectives": self.active_objectives,
            "projects": self.projects,
            "tasks": self.tasks,
            "habits": self.habits,
        }


@dataclass
class HierarchyNode:
    kind: str
    id: uuid.UUID
    title: str
    attributes: dict[str, Any]
    children: list[HierarchyNode] = field(default_factory=list["HierarchyNode"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": str(self.id),
            "title": self.title,
            **self.attributes,
            "children": [c.as_dict() for c in self.children],
        }


# --------------------------------------------------------------------------- service


class GoalService:
    def __init__(self, clock: Clock, cancel_hooks: list[CancelHook] | None = None) -> None:
        self._clock = clock
        self._cancel_hooks = list(cancel_hooks or [])

    async def create(self, s: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]) -> m.Goal:
        now = self._clock.now()
        goal = m.Goal(
            id=uuid.uuid4(),
            user_id=user_id,
            title=data["title"],
            category=data["category"],
            description=data.get("description"),
            target_date=data.get("target_date"),
            priority=data.get("priority", 3),
            created_at=now,
            updated_at=now,
        )
        s.add(goal)
        await s.flush()
        return goal

    async def list_goals(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        status: GoalStatus | None = None,
        category: GoalCategory | None = None,
    ) -> list[m.Goal]:
        query = select(m.Goal).where(m.Goal.user_id == user_id, m.Goal.deleted_at.is_(None))
        if status is not None:
            query = query.where(m.Goal.status == status)
        if category is not None:
            query = query.where(m.Goal.category == category)
        return sort_by_priority(list((await s.execute(query)).scalars()))

    async def list_active(self, s: AsyncSession, user_id: uuid.UUID) -> list[m.Goal]:
        return await self.list_goals(s, user_id, status="active")

    async def update(
        self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID, changes: dict[str, Any]
    ) -> m.Goal:
        goal = await get_goal(s, user_id, goal_id)
        if changes.get("status") == "archived":
            raise DomainError(
                "VALIDATION_ERROR", "Use the archive action to archive a goal", {"field": "status"}
            )
        ensure_writable(goal)
        for key in ("title", "category", "description", "target_date", "priority", "status"):
            if key in changes:
                setattr(goal, key, changes[key])
        goal.updated_at = self._clock.now()
        await s.flush()
        return goal

    async def set_priority(
        self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID, priority: int
    ) -> m.Goal:
        if not 1 <= priority <= 5:
            raise DomainError("VALIDATION_ERROR", "Priority must be between 1 and 5", {"field": "priority"})
        return await self.update(s, user_id, goal_id, {"priority": priority})

    async def archive(self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID) -> m.Goal:
        """R1.14: preserve everything, read-only. Future planned Daily Actions are cancelled in M5 (T5.x)."""
        goal = await get_goal(s, user_id, goal_id)
        if goal.status != "archived":
            now = self._clock.now()
            goal.status = "archived"
            goal.archived_at = now
            goal.updated_at = now
            await self._cancel_future_actions(s, user_id, goal.id, now)
            await s.flush()
        return goal

    async def _cancel_future_actions(
        self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID, now: datetime
    ) -> None:
        """Future Planned Daily Actions of the archived subtree are cancelled; history stays intact."""
        objective_ids = select(m.Objective.id).where(m.Objective.goal_id == goal_id)
        project_ids = select(m.Project.id).where(m.Project.objective_id.in_(objective_ids))
        task_ids = select(m.Task.id).where(or_(m.Task.goal_id == goal_id, m.Task.project_id.in_(project_ids)))
        habit_ids = select(m.Habit.id).where(
            or_(m.Habit.goal_id == goal_id, m.Habit.project_id.in_(project_ids))
        )
        future = (
            await s.execute(
                select(m.DailyAction).where(
                    m.DailyAction.user_id == user_id,
                    m.DailyAction.lifecycle_state == "active",
                    m.DailyAction.status == "Planned",
                    m.DailyAction.scheduled_start > now,
                    or_(
                        (m.DailyAction.source_type == "TASK") & m.DailyAction.source_id.in_(task_ids),
                        (m.DailyAction.source_type == "HABIT") & m.DailyAction.source_id.in_(habit_ids),
                    ),
                )
            )
        ).scalars()
        cancelled = list(future)
        for action in cancelled:
            system_cancel(s, action, "cancelled", "goal_archived", now)
        await s.flush()
        for hook in self._cancel_hooks:
            await hook(s, user_id, cancelled, "System", "goal_archived")

    async def cascade_counts(self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID) -> CascadeCounts:
        live_objectives = select(m.Objective.id).where(
            m.Objective.goal_id == goal_id, m.Objective.user_id == user_id, m.Objective.deleted_at.is_(None)
        )
        project_ids = select(m.Project.id).where(
            m.Project.objective_id.in_(live_objectives), m.Project.deleted_at.is_(None)
        )

        async def count(query: Any) -> int:
            return int((await s.execute(select(func.count()).select_from(query.subquery()))).scalar_one())

        return CascadeCounts(
            objectives=await count(live_objectives),
            active_objectives=await count(live_objectives.where(m.Objective.status == "active")),
            projects=await count(project_ids),
            tasks=await count(
                select(m.Task.id).where(
                    m.Task.user_id == user_id,
                    m.Task.deleted_at.is_(None),
                    or_(m.Task.goal_id == goal_id, m.Task.project_id.in_(project_ids)),
                )
            ),
            habits=await count(
                select(m.Habit.id).where(
                    m.Habit.user_id == user_id,
                    m.Habit.deleted_at.is_(None),
                    or_(m.Habit.goal_id == goal_id, m.Habit.project_id.in_(project_ids)),
                )
            ),
        )

    async def delete(
        self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID, *, confirm_cascade: bool
    ) -> CascadeCounts:
        """R1.15: confirmation required when active Objectives or Projects exist; soft-deletes the subtree.

        Daily Actions, Check-in Records and Commitments are preserved for history (design §24.3).
        """
        goal = await get_goal(s, user_id, goal_id)
        counts = await self.cascade_counts(s, user_id, goal.id)
        if (counts.active_objectives or counts.projects) and not confirm_cascade:
            raise DomainError(
                "CASCADE_CONFIRMATION_REQUIRED",
                "Deleting this goal also deletes its objectives, projects, tasks and habits",
                counts.as_dict(),
            )
        now = self._clock.now()
        objective_ids = select(m.Objective.id).where(
            m.Objective.goal_id == goal.id, m.Objective.user_id == user_id
        )
        project_ids = select(m.Project.id).where(m.Project.objective_id.in_(objective_ids))
        for model, condition in (
            (m.Task, or_(m.Task.goal_id == goal.id, m.Task.project_id.in_(project_ids))),
            (m.Habit, or_(m.Habit.goal_id == goal.id, m.Habit.project_id.in_(project_ids))),
            (m.Project, m.Project.id.in_(project_ids)),
            (m.Objective, m.Objective.goal_id == goal.id),
            (m.Goal, m.Goal.id == goal.id),
        ):
            await s.execute(
                update(model)
                .where(condition, model.user_id == user_id, model.deleted_at.is_(None))
                .values(deleted_at=now, updated_at=now)
            )
        return counts

    async def hierarchy(self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID) -> HierarchyNode:
        """R1.16 tree: Goal → Objective → Project → Task, with Habits under their Goal/Project.

        Daily Actions are attached beneath Tasks/Habits once scheduling exists (M5).
        """
        goal = await get_goal(s, user_id, goal_id)
        root = HierarchyNode(
            "goal",
            goal.id,
            goal.title,
            {
                "category": goal.category,
                "status": goal.status,
                "priority": goal.priority,
                "progress": str(goal.progress),
            },
        )
        objectives = list(
            (
                await s.execute(
                    select(m.Objective)
                    .where(m.Objective.goal_id == goal.id, m.Objective.deleted_at.is_(None))
                    .order_by(m.Objective.created_at, m.Objective.id)
                )
            ).scalars()
        )
        projects = list(
            (
                await s.execute(
                    select(m.Project)
                    .where(
                        m.Project.objective_id.in_([o.id for o in objectives]), m.Project.deleted_at.is_(None)
                    )
                    .order_by(m.Project.created_at, m.Project.id)
                )
            ).scalars()
        )
        project_ids = [p.id for p in projects]
        tasks = list(
            (
                await s.execute(
                    select(m.Task)
                    .where(
                        m.Task.deleted_at.is_(None),
                        or_(m.Task.goal_id == goal.id, m.Task.project_id.in_(project_ids)),
                    )
                    .order_by(m.Task.created_at, m.Task.id)
                )
            ).scalars()
        )
        habits = list(
            (
                await s.execute(
                    select(m.Habit)
                    .where(
                        m.Habit.deleted_at.is_(None),
                        or_(m.Habit.goal_id == goal.id, m.Habit.project_id.in_(project_ids)),
                    )
                    .order_by(m.Habit.created_at, m.Habit.id)
                )
            ).scalars()
        )

        project_nodes: dict[uuid.UUID, HierarchyNode] = {}
        for objective in objectives:
            node = HierarchyNode(
                "objective",
                objective.id,
                objective.title,
                {"status": objective.status, "progress": str(objective.progress), "unit": objective.unit},
            )
            root.children.append(node)
            for project in (p for p in projects if p.objective_id == objective.id):
                project_nodes[project.id] = HierarchyNode(
                    "project", project.id, project.title, {"status": project.status}
                )
                node.children.append(project_nodes[project.id])
        for task in tasks:
            leaf = HierarchyNode(
                "task", task.id, task.title, {"status": task.status, "due_date": _iso(task.due_date)}
            )
            parent = project_nodes.get(task.project_id) if task.project_id else None
            (parent or root).children.append(leaf)
        for habit in habits:
            leaf = HierarchyNode("habit", habit.id, habit.title, {"recurrence_type": habit.recurrence_type})
            parent = project_nodes.get(habit.project_id) if habit.project_id else None
            (parent or root).children.append(leaf)
        return root


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


# --------------------------------------------------------------------------- projects


class ProjectService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def create(
        self, s: AsyncSession, user_id: uuid.UUID, objective_id: uuid.UUID, data: dict[str, Any]
    ) -> m.Project:
        ensure_writable(await goal_of_objective(s, user_id, objective_id))
        now = self._clock.now()
        project = m.Project(
            id=uuid.uuid4(),
            user_id=user_id,
            objective_id=objective_id,
            title=data["title"],
            description=data.get("description"),
            created_at=now,
            updated_at=now,
        )
        s.add(project)
        await s.flush()
        return project

    async def get(self, s: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID) -> m.Project:
        return await get_project(s, user_id, project_id)

    async def list_for_objective(
        self, s: AsyncSession, user_id: uuid.UUID, objective_id: uuid.UUID
    ) -> list[m.Project]:
        await goal_of_objective(s, user_id, objective_id)  # 404 when not owned
        return list(
            (
                await s.execute(
                    select(m.Project)
                    .where(m.Project.objective_id == objective_id, m.Project.deleted_at.is_(None))
                    .order_by(m.Project.created_at, m.Project.id)
                )
            ).scalars()
        )

    async def update(
        self, s: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID, changes: dict[str, Any]
    ) -> m.Project:
        project = await get_project(s, user_id, project_id)
        ensure_writable(await goal_of_project(s, user_id, project))
        for key in ("title", "description", "status"):
            if key in changes:
                setattr(project, key, changes[key])
        project.updated_at = self._clock.now()
        await s.flush()
        return project

    async def delete(self, s: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = await get_project(s, user_id, project_id)
        ensure_writable(await goal_of_project(s, user_id, project))
        now = self._clock.now()
        for model, condition in (
            (m.Task, m.Task.project_id == project.id),
            (m.Habit, m.Habit.project_id == project.id),
            (m.Project, m.Project.id == project.id),
        ):
            await s.execute(
                update(model)
                .where(condition, model.user_id == user_id, model.deleted_at.is_(None))
                .values(deleted_at=now, updated_at=now)
            )
