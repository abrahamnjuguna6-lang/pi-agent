"""Objective and Goal progress (design §16.1, R1.3–1.7). Deterministic; never delegated to an LLM.

Pure functions compute progress with `Decimal`; `recompute_goal()` persists it in the caller's
transaction whenever an Objective's value, weight, status or target changes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.errors import DomainError

MetricDirection = Literal["higher_is_better", "lower_is_better"]
ZERO = Decimal(0)
HUNDRED = Decimal(100)
CENT = Decimal("0.01")


def clamp_percent(value: Decimal) -> Decimal:
    return max(ZERO, min(HUNDRED, value))


def quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def validate_range(direction: MetricDirection, target: Decimal, baseline: Decimal | None) -> None:
    """R1.3: reject ranges that would divide by zero. Nothing is saved on failure."""
    if direction == "higher_is_better":
        if target == 0:
            raise DomainError(
                "VALIDATION_ERROR",
                "Target value must not be zero for a higher-is-better objective",
                {"field": "target_value"},
            )
        return
    if baseline is None:
        raise DomainError(
            "VALIDATION_ERROR",
            "A baseline value is required for a lower-is-better objective",
            {"field": "baseline_value"},
        )
    if baseline == target:
        raise DomainError(
            "VALIDATION_ERROR",
            "Baseline and target must differ for a lower-is-better objective",
            {"field": "baseline_value"},
        )


def objective_progress(
    direction: MetricDirection, current: Decimal, target: Decimal, baseline: Decimal | None
) -> Decimal:
    """R1.4 / R1.5 — clamped to 0–100 and rounded to 2 dp."""
    validate_range(direction, target, baseline)
    if direction == "higher_is_better":
        raw = current / target * HUNDRED
    else:
        assert baseline is not None  # guaranteed by validate_range
        raw = (baseline - current) / (baseline - target) * HUNDRED
    return quantize(clamp_percent(raw))


@dataclass(frozen=True)
class WeightedProgress:
    progress: Decimal
    weight: Decimal


def goal_progress(objectives: list[WeightedProgress]) -> Decimal:
    """R1.6 / R1.7 — weighted mean of ACTIVE objectives; 0 when there are none."""
    total_weight = sum((o.weight for o in objectives), ZERO)
    if not objectives or total_weight <= 0:
        return quantize(ZERO)
    weighted = sum((o.progress * o.weight for o in objectives), ZERO)
    return quantize(clamp_percent(weighted / total_weight))


async def recompute_goal(session: AsyncSession, goal_id: uuid.UUID) -> Decimal:
    """Recompute and persist a Goal's progress from its active, non-deleted Objectives."""
    rows = (
        await session.execute(
            select(m.Objective.progress, m.Objective.weight).where(
                m.Objective.goal_id == goal_id,
                m.Objective.status == "active",
                m.Objective.deleted_at.is_(None),
            )
        )
    ).all()
    value = goal_progress([WeightedProgress(Decimal(p), Decimal(w)) for p, w in rows])
    goal = (await session.execute(select(m.Goal).where(m.Goal.id == goal_id))).scalar_one()
    goal.progress = value
    return value
