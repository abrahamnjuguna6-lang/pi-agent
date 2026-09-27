"""Item lineage: why does this exist? (design §16.9; R13.7, R9.15).

Walks an item up to its Goal:
    Daily Action → its source (Task, Habit or Routine Entry; a Routine Entry's `habit_id` is followed)
    Task / Habit → Project and/or Goal
    Project      → Objective → Goal
The chain carries titles, progress and priority so the agent can explain the link from stored facts
rather than inferring one. When nothing links the item to a Goal the chain ends with an explicit
`unlinked` marker, so the agent says "this is not linked to a goal" instead of inventing a purpose.
`goal_category` on a Commitment is derived from the same walk (design §11.6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.errors import NotFoundError
from lifeos.domain.goals import get_goal, get_project
from lifeos.domain.objectives import get_objective
from lifeos.domain.schedule.common import get_action, goal_of_source, source_goal_id
from lifeos.domain.tasks import get_task

MODELS: dict[str, Any] = {
    "daily_action": m.DailyAction,
    "task": m.Task,
    "habit": m.Habit,
    "routine_entry": m.RoutineEntry,
    "project": m.Project,
    "objective": m.Objective,
    "goal": m.Goal,
}

EntityType = Literal["Goal", "Objective", "Project", "Task", "DailyAction"]
NodeType = Literal["daily_action", "task", "habit", "routine_entry", "project", "objective", "goal"]
TargetType = Literal["daily_action", "task", "habit", "routine_entry", "project", "objective", "goal"]
UNLINKED = "unlinked"
SOURCE_NODES: dict[str, NodeType] = {
    "TASK": "task",
    "HABIT": "habit",
    "ROUTINE_ENTRY": "routine_entry",
}


@dataclass(frozen=True)
class LineageNode:
    type: NodeType
    id: uuid.UUID
    title: str
    progress: Decimal | None = None
    priority: int | None = None
    category: str | None = None
    status: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": str(self.id),
            "title": self.title,
            "progress": None if self.progress is None else str(self.progress),
            "priority": self.priority,
            "category": self.category,
            "status": self.status,
        }


@dataclass(frozen=True)
class Lineage:
    """`chain` runs from the item to its Goal. `unlinked` means the walk never reached one."""

    chain: list[LineageNode]
    goal_category: str | None = None

    @property
    def unlinked(self) -> bool:
        return not self.chain or self.chain[-1].type != "goal"

    @property
    def goal(self) -> LineageNode | None:
        return self.chain[-1] if self.chain and self.chain[-1].type == "goal" else None

    def as_json(self) -> dict[str, Any]:
        return {
            "chain": [node.as_json() for node in self.chain],
            "goal_category": self.goal_category,
            "unlinked": self.unlinked,
            "marker": UNLINKED if self.unlinked else None,
        }


# --------------------------------------------------------------------------- goal resolution


async def goal_of_entity(
    s: AsyncSession, user_id: uuid.UUID, entity_type: EntityType, entity_id: uuid.UUID
) -> uuid.UUID | None:
    """The Goal an entity belongs to (None when unlinked). Raises NotFoundError for a missing entity
    or one owned by another User."""
    if entity_type == "Goal":
        return (await get_goal(s, user_id, entity_id)).id
    if entity_type == "Objective":
        return (await get_objective(s, user_id, entity_id)).goal_id
    if entity_type == "Project":
        return await _goal_of_objective(s, (await get_project(s, user_id, entity_id)).objective_id)
    if entity_type == "Task":
        task = await get_task(s, user_id, entity_id)
        if task.goal_id is not None:
            return task.goal_id
        if task.project_id is None:
            return None
        return await _goal_of_objective(s, (await get_project(s, user_id, task.project_id)).objective_id)
    return await source_goal_id(s, await get_action(s, user_id, entity_id))


async def goal_category(s: AsyncSession, goal_id: uuid.UUID | None) -> str | None:
    if goal_id is None:
        return None
    return (await s.execute(select(m.Goal.category).where(m.Goal.id == goal_id))).scalar_one_or_none()


async def _goal_of_objective(s: AsyncSession, objective_id: uuid.UUID) -> uuid.UUID | None:
    return (
        await s.execute(select(m.Objective.goal_id).where(m.Objective.id == objective_id))
    ).scalar_one_or_none()


# --------------------------------------------------------------------------- the full chain (§16.9)


class LineageService:
    """The walk is tolerant of missing parents on purpose: a Daily Action whose Goal was deleted still
    exists, and the honest answer is "this is no longer linked to a goal" rather than a 404. Only the
    item being asked about must exist and belong to the User."""

    async def lineage(
        self, s: AsyncSession, user_id: uuid.UUID, target_type: TargetType, target_id: uuid.UUID
    ) -> Lineage:
        chain: list[LineageNode] = []
        node: tuple[TargetType, uuid.UUID] | None = (target_type, target_id)
        strict = True
        while node is not None:
            node_type, node_id = node
            node = await self._step(s, user_id, node_type, node_id, chain, strict=strict)
            strict = False
        goal = chain[-1] if chain and chain[-1].type == "goal" else None
        return Lineage(chain, goal.category if goal else None)

    async def _step(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        node_type: TargetType,
        node_id: uuid.UUID,
        chain: list[LineageNode],
        *,
        strict: bool,
    ) -> tuple[TargetType, uuid.UUID] | None:
        """Append one node and return the next one to visit, or None when the walk ends."""
        row = await self._row(s, user_id, node_type, node_id)
        if row is None:
            if strict:
                raise NotFoundError(node_type.replace("_", " "))
            return None  # a deleted parent ends the chain; `Lineage.unlinked` reports it
        chain.append(self._node(node_type, row))
        return self._parent(node_type, row)

    async def _row(
        self, s: AsyncSession, user_id: uuid.UUID, node_type: TargetType, node_id: uuid.UUID
    ) -> Any:
        model = MODELS[node_type]
        query = select(model).where(model.id == node_id, model.user_id == user_id)
        if hasattr(model, "deleted_at"):
            query = query.where(model.deleted_at.is_(None))
        return (await s.execute(query)).scalar_one_or_none()

    def _node(self, node_type: TargetType, row: Any) -> LineageNode:
        return LineageNode(
            type=node_type,
            id=row.id,
            title=row.title,
            progress=getattr(row, "progress", None),
            priority=getattr(row, "priority", None),
            category=getattr(row, "category", None),
            status=getattr(row, "status", None),
        )

    def _parent(self, node_type: TargetType, row: Any) -> tuple[TargetType, uuid.UUID] | None:
        """Where this node rolls up to (design §16.9)."""
        if node_type == "daily_action":
            source = SOURCE_NODES.get(row.source_type)
            return None if source is None or row.source_id is None else (source, row.source_id)
        if node_type == "routine_entry":
            if row.habit_id is not None:  # a routine entry may run a Habit
                return "habit", row.habit_id
            return ("goal", row.goal_id) if row.goal_id is not None else None
        if node_type in ("task", "habit"):
            if row.goal_id is not None:
                return "goal", row.goal_id
            return ("project", row.project_id) if row.project_id is not None else None
        if node_type == "project":
            return "objective", row.objective_id
        if node_type == "objective":
            return "goal", row.goal_id
        return None  # a Goal is the top of the chain


async def category_of_action(s: AsyncSession, action: m.DailyAction) -> str | None:
    """Goal category for Completion-Rate-by-category attribution (design §16.10)."""
    return await goal_category(s, await goal_of_source(s, action.source_type, action.source_id))
