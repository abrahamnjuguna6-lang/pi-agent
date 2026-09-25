"""Entity → Goal lineage (design §16.9).

> M6 note: the minimal resolver the Promise Ledger needs to derive `goal_category` (design §11.6).
> T8.3's LineageService (full "Why?" chains) builds on it.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.goals import get_goal, get_project
from lifeos.domain.objectives import get_objective
from lifeos.domain.schedule.common import get_action, source_goal_id
from lifeos.domain.tasks import get_task

EntityType = Literal["Goal", "Objective", "Project", "Task", "DailyAction"]


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
