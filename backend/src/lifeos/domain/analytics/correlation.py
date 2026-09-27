# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportAttributeAccessIssue=false
# SciPy ships no type stubs, so its result objects are untyped here. Every value crossing back into
# typed code goes through `_finite()` or `bool()`, so nothing untyped escapes this module.
"""Reflection correlation analysis (design §16.5; R11.6–11.9).

An observation is a local day with both a self-reported value (energy or mood) and a Completion Rate.
    n < 7   → nothing is computed or shown
    7 ≤ n < 20 → a descriptive, explicitly preliminary observation; no significance claim
    n ≥ 20  → two-tailed Spearman; a finding is surfaced only when p < 0.05
Spearman is the default because it assumes no distribution. Pearson is computed only when
Shapiro–Wilk gives p > 0.05 on both variables, and is reported alongside, never instead.

Every surfaced finding carries the coefficient, n and the p-value, and the caller states that
correlation does not imply causation (R11.9) — this module never phrases the finding itself.
Results are stored in `analytics_results` by the nightly `analytics_refresh` job (T10.2).
"""

from __future__ import annotations

import math
import uuid
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from scipy import stats
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.analytics.completion import daily_rates
from lifeos.domain.clock import Clock

Status = Literal["none", "preliminary", "not_significant", "significant"]
Method = Literal["spearman", "pearson"]
AnalyticsKind = Literal["energy_completion_corr", "mood_completion_corr"]

MIN_PRELIMINARY = 7
MIN_SIGNIFICANT = 20
ALPHA = 0.05
MIN_SHAPIRO = 3  # Shapiro–Wilk is undefined below three observations
LOOKBACK_DAYS = 90  # how far back observations are gathered (a quarter of self-reports)
KINDS: dict[str, AnalyticsKind] = {
    "energy": "energy_completion_corr",
    "mood": "mood_completion_corr",
}


@dataclass(frozen=True)
class Correlation:
    """One correlation result. `coefficient`/`p_value` are None when the sample was too small or the
    coefficient is undefined (a constant variable has no rank correlation)."""

    n: int
    status: Status
    method: Method | None = None
    coefficient: float | None = None
    p_value: float | None = None
    pearson: Correlation | None = None  # only when normality holds (design §16.5)

    @property
    def surfaceable(self) -> bool:
        """R11.8: only a significant finding may be presented as one."""
        return self.status == "significant"

    def as_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "method": self.method,
            "n": self.n,
            "coefficient": self.coefficient,
            "p_value": self.p_value,
            "status": self.status,
        }
        if self.pearson is not None:
            data["pearson"] = self.pearson.as_json()
        return data


def _finite(value: float) -> float | None:
    return None if math.isnan(value) else float(value)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> tuple[float | None, float | None]:
    """Coefficient and two-tailed p-value; (None, None) when a variable is constant.

    A constant series is normal input here (a week of identical energy scores), not an anomaly, so
    scipy's warning about it is suppressed and reported as "no coefficient" instead."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", stats.ConstantInputWarning)
        result = stats.spearmanr(list(xs), list(ys))
    return _finite(float(result.statistic)), _finite(float(result.pvalue))


def is_normal(values: Sequence[float]) -> bool:
    """Shapiro–Wilk with p > 0.05. A constant variable is never treated as normal."""
    if len(values) < MIN_SHAPIRO or len(set(values)) == 1:
        return False
    return bool(stats.shapiro(list(values)).pvalue > ALPHA)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> tuple[float | None, float | None]:
    result = stats.pearsonr(list(xs), list(ys))
    return _finite(float(result.statistic)), _finite(float(result.pvalue))


def correlate(xs: Sequence[float], ys: Sequence[float]) -> Correlation:
    """The deterministic finding for a pair of series (design §16.5). Never raises on degenerate
    input: a constant series simply has no coefficient."""
    if len(xs) != len(ys):
        raise ValueError("correlation needs paired observations")
    n = len(xs)
    if n < MIN_PRELIMINARY:
        return Correlation(n=n, status="none")

    coefficient, p_value = spearman(xs, ys)
    if n < MIN_SIGNIFICANT:
        # Descriptive only: shown with the "insufficient data" label, with no significance claim.
        return Correlation(n=n, status="preliminary", method="spearman", coefficient=coefficient)
    if coefficient is None or p_value is None:
        return Correlation(n=n, status="not_significant", method="spearman")

    status: Status = "significant" if p_value < ALPHA else "not_significant"
    normal = is_normal(xs) and is_normal(ys)
    extra: Correlation | None = None
    if normal:
        pearson_coefficient, pearson_p = pearson(xs, ys)
        extra = Correlation(
            n=n,
            status="significant" if pearson_p is not None and pearson_p < ALPHA else "not_significant",
            method="pearson",
            coefficient=pearson_coefficient,
            p_value=pearson_p,
        )
    return Correlation(
        n=n,
        status=status,
        method="spearman",
        coefficient=coefficient,
        p_value=p_value,
        pearson=extra,
    )


@dataclass(frozen=True)
class Observation:
    day: date
    energy: int | None
    mood: int | None
    completion_rate: float


class CorrelationService:
    def __init__(self, clock: Clock, lookback_days: int = LOOKBACK_DAYS) -> None:
        self._clock = clock
        self._lookback = lookback_days

    async def observations(self, s: AsyncSession, user_id: uuid.UUID, end: date) -> list[Observation]:
        """Local days in the lookback window with a self-report and a Completion Rate (design §16.5)."""
        from sqlalchemy import select

        start = end - timedelta(days=self._lookback - 1)
        rates = await daily_rates(s, user_id, start, end, self._clock.now())
        reflections = (
            await s.execute(
                select(m.Reflection.date, m.Reflection.energy, m.Reflection.mood)
                .where(
                    m.Reflection.user_id == user_id,
                    m.Reflection.type == "daily",
                    m.Reflection.deleted_at.is_(None),
                    m.Reflection.date >= start,
                    m.Reflection.date <= end,
                )
                .order_by(m.Reflection.date)
            )
        ).tuples()
        observations: list[Observation] = []
        for day, energy, mood in reflections:
            rate = rates.get(day)
            if rate is None or rate.rate is None:  # no scheduled actions that day → no observation
                continue
            observations.append(Observation(day, energy, mood, float(rate.rate)))
        return observations

    def correlate_metric(self, observations: Sequence[Observation], metric: str) -> Correlation:
        pairs = [
            (getattr(observation, metric), observation.completion_rate)
            for observation in observations
            if getattr(observation, metric) is not None
        ]
        return correlate([float(value) for value, _ in pairs], [rate for _, rate in pairs])

    async def refresh(
        self, s: AsyncSession, user_id: uuid.UUID, computed_for: date
    ) -> dict[str, Correlation]:
        """Recompute both correlations for a User and store them (`analytics_refresh`, T10.2)."""
        observations = await self.observations(s, user_id, computed_for)
        results: dict[str, Correlation] = {}
        now = self._clock.now()
        for metric, kind in KINDS.items():
            result = self.correlate_metric(observations, metric)
            results[metric] = result
            values = {"result": result.as_json(), "computed_at": now}
            await s.execute(
                insert(m.AnalyticsResult)
                .values(user_id=user_id, kind=kind, computed_for=computed_for, **values)
                .on_conflict_do_update(index_elements=["user_id", "kind", "computed_for"], set_=values)
            )
        await s.flush()
        return results
