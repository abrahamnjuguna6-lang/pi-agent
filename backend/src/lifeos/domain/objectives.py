"""Objectives: CRUD, range validation, value updates with progress recompute (design §16.1; R1.2–1.7)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.goals import ensure_writable, get_goal
from lifeos.domain.progress import objective_progress, recompute_goal
from lifeos.events.outbox import publish

Actor = Literal["User", "Agent", "System"]
EDITABLE = (
    "title",
    "metric_direction",
    "current_value",
    "target_value",
    "baseline_value",
    "unit",
    "weight",
    "target_date",
    "status",
)
# Changes that alter progress (design §16.1: recompute on value, weight, status or target change).
PROGRESS_FIELDS = frozenset(
    {"metric_direction", "current_value", "target_value", "baseline_value", "weight", "status"}
)


async def get_objective(s: AsyncSession, user_id: uuid.UUID, objective_id: uuid.UUID) -> m.Objective:
    objective = (
        await s.execute(
            select(m.Objective).where(
                m.Objective.id == objective_id,
                m.Objective.user_id == user_id,
                m.Objective.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if objective is None:
        raise NotFoundError("objective")
    return objective


class ObjectiveService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def _apply_progress(self, s: AsyncSession, objective: m.Objective, actor: Actor) -> None:
        """Recompute objective + goal progress, append history, publish the event — same transaction."""
        objective.progress = objective_progress(
            objective.metric_direction,  # type: ignore[arg-type]
            Decimal(objective.current_value),
            Decimal(objective.target_value),
            None if objective.baseline_value is None else Decimal(objective.baseline_value),
        )
        await s.flush()
        goal_progress = await recompute_goal(s, objective.goal_id)
        now = self._clock.now()
        s.add(
            m.ObjectiveValueHistory(
                objective_id=objective.id,
                user_id=objective.user_id,
                current_value=objective.current_value,
                progress=objective.progress,
                changed_by=actor,
                changed_at=now,
            )
        )
        await publish(
            s,
            "objective.value_changed",
            objective.user_id,
            {
                "objective_id": str(objective.id),
                "goal_id": str(objective.goal_id),
                "progress": str(objective.progress),
                "goal_progress": str(goal_progress),
                "actor": actor,
            },
            at=now,
        )

    async def create(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        goal_id: uuid.UUID,
        data: dict[str, Any],
        actor: Actor = "User",
    ) -> m.Objective:
        ensure_writable(await get_goal(s, user_id, goal_id))
        now = self._clock.now()
        objective = m.Objective(
            id=uuid.uuid4(),
            goal_id=goal_id,
            user_id=user_id,
            title=data["title"],
            metric_direction=data["metric_direction"],
            current_value=data["current_value"],
            target_value=data["target_value"],
            baseline_value=data.get("baseline_value"),
            unit=data["unit"],
            weight=data.get("weight", Decimal(1)),
            target_date=data["target_date"],
            created_at=now,
            updated_at=now,
        )
        _validate_weight(objective.weight)
        s.add(objective)
        await self._apply_progress(s, objective, actor)  # validates the range before anything is flushed
        return objective

    async def get(self, s: AsyncSession, user_id: uuid.UUID, objective_id: uuid.UUID) -> m.Objective:
        return await get_objective(s, user_id, objective_id)

    async def list_for_goal(
        self, s: AsyncSession, user_id: uuid.UUID, goal_id: uuid.UUID
    ) -> list[m.Objective]:
        await get_goal(s, user_id, goal_id)
        return list(
            (
                await s.execute(
                    select(m.Objective)
                    .where(m.Objective.goal_id == goal_id, m.Objective.deleted_at.is_(None))
                    .order_by(m.Objective.created_at, m.Objective.id)
                )
            ).scalars()
        )

    async def update(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        objective_id: uuid.UUID,
        changes: dict[str, Any],
        actor: Actor = "User",
    ) -> m.Objective:
        objective = await get_objective(s, user_id, objective_id)
        ensure_writable(await get_goal(s, user_id, objective.goal_id))
        for key in EDITABLE:
            if key in changes:
                if changes[key] is None and key != "baseline_value":
                    raise DomainError("VALIDATION_ERROR", f"{key} cannot be cleared", {"field": key})
                setattr(objective, key, changes[key])
        _validate_weight(objective.weight)
        objective.updated_at = self._clock.now()
        if PROGRESS_FIELDS & set(changes):
            await self._apply_progress(s, objective, actor)
        else:
            await s.flush()
        return objective

    async def set_value(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        objective_id: uuid.UUID,
        value: Decimal,
        actor: Actor = "User",
    ) -> m.Objective:
        return await self.update(s, user_id, objective_id, {"current_value": value}, actor)

    async def delete(self, s: AsyncSession, user_id: uuid.UUID, objective_id: uuid.UUID) -> None:
        objective = await get_objective(s, user_id, objective_id)
        ensure_writable(await get_goal(s, user_id, objective.goal_id))
        now = self._clock.now()
        objective.deleted_at = now
        objective.updated_at = now
        await s.flush()
        await recompute_goal(s, objective.goal_id)


def _validate_weight(weight: Decimal) -> None:
    if Decimal(weight) <= 0:
        raise DomainError("VALIDATION_ERROR", "Weight must be positive", {"field": "weight"})
