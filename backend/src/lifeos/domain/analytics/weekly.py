"""Weekly aggregates: Completion Rate by category, wins and gaps (design §16.10; R10.2).

Over the prior 7 local days (Monday–Sunday), for the Weekly CEO Meeting's pre-session briefing:
    wins — sources and Tasks with Completed actions, Kept Commitments, Objectives that moved,
           ranked by linked-Goal priority, then completion count, then progress delta
    gaps — sources with Skipped or incomplete actions, Broken Commitments, and Objectives with no
           progress whose target date is within 30 days, ranked by skip + incomplete count, then
           linked-Goal priority
    completion rate by Goal category, with unattributable actions reported as "Unlinked"
Top 3 of each. Everything here is deterministic; the Mentor agent only narrates it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.analytics.completion import CompletionRate, compute, load_snapshots
from lifeos.domain.clock import Clock
from lifeos.domain.integrity import IntegrityService
from lifeos.domain.lineage import category_of_action
from lifeos.domain.schedule.common import goal_of_source, user_timezone
from lifeos.domain.timeutil import iso_week_start, local_day_bounds

TOP_N = 3
OBJECTIVE_DEADLINE_DAYS = 30
UNLINKED = "Unlinked"
WinKind = Literal["source", "task", "commitment", "objective"]
GapKind = Literal["source", "commitment", "objective"]


@dataclass(frozen=True)
class Win:
    kind: WinKind
    id: uuid.UUID
    title: str
    completions: int = 0
    goal_priority: int = 0
    progress_delta: Decimal = Decimal(0)

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": str(self.id),
            "title": self.title,
            "completions": self.completions,
            "goal_priority": self.goal_priority,
            "progress_delta": str(self.progress_delta),
        }


@dataclass(frozen=True)
class Gap:
    kind: GapKind
    id: uuid.UUID
    title: str
    misses: int = 0
    goal_priority: int = 0
    detail: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": str(self.id),
            "title": self.title,
            "misses": self.misses,
            "goal_priority": self.goal_priority,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CategoryRate:
    category: str
    rate: Decimal | None
    completed: int
    total: int

    def as_json(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "rate": None if self.rate is None else str(self.rate),
            "completed": self.completed,
            "total": self.total,
        }


@dataclass(frozen=True)
class WeeklySummary:
    week_start: date
    week_end: date
    completion_rate: CompletionRate
    by_category: list[CategoryRate]
    wins: list[Win]
    gaps: list[Gap]
    integrity_score: Decimal | None = None
    integrity_delta: Decimal | None = None
    kept_commitments: int = 0
    broken_commitments: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "week_start": self.week_start.isoformat(),
            "week_end": self.week_end.isoformat(),
            "completion_rate": None if self.completion_rate.rate is None else str(self.completion_rate.rate),
            "by_category": [row.as_json() for row in self.by_category],
            "wins": [win.as_json() for win in self.wins],
            "gaps": [gap.as_json() for gap in self.gaps],
            "integrity_score": None if self.integrity_score is None else str(self.integrity_score),
            "integrity_delta": None if self.integrity_delta is None else str(self.integrity_delta),
            "kept_commitments": self.kept_commitments,
            "broken_commitments": self.broken_commitments,
        }


# --------------------------------------------------------------------------- pure ranking (§16.10)


def rank_wins(wins: Sequence[Win], top: int = TOP_N) -> list[Win]:
    """Goal priority, then completion count, then progress delta; ties break on title then id so the
    order is stable between runs."""
    return sorted(
        wins,
        key=lambda w: (-w.goal_priority, -w.completions, -w.progress_delta, w.title, str(w.id)),
    )[:top]


def rank_gaps(gaps: Sequence[Gap], top: int = TOP_N) -> list[Gap]:
    """Miss count first (skips plus incomplete), then Goal priority."""
    return sorted(gaps, key=lambda g: (-g.misses, -g.goal_priority, g.title, str(g.id)))[:top]


def category_rates(counts: dict[str, tuple[int, int]]) -> list[CategoryRate]:
    """`{category: (completed, total)}` → rates, highest first; "Unlinked" always sorts last."""
    rows = [
        CategoryRate(
            category=category,
            rate=None if total == 0 else (Decimal(completed) / Decimal(total)).quantize(Decimal("0.0001")),
            completed=completed,
            total=total,
        )
        for category, (completed, total) in counts.items()
    ]
    return sorted(rows, key=lambda row: (row.category == UNLINKED, -(row.rate or Decimal(0)), row.category))


def week_bounds(day: date) -> tuple[date, date]:
    """The Monday–Sunday week that `day` falls in."""
    monday = iso_week_start(day)
    return monday, monday + timedelta(days=6)


def previous_week(day: date) -> tuple[date, date]:
    monday, _ = week_bounds(day)
    start = monday - timedelta(days=7)
    return start, start + timedelta(days=6)


# --------------------------------------------------------------------------- service


class WeeklyAnalyticsService:
    def __init__(self, clock: Clock, integrity: IntegrityService) -> None:
        self._clock = clock
        self._integrity = integrity

    async def summarize(
        self, s: AsyncSession, user_id: uuid.UUID, week_start: date | None = None
    ) -> WeeklySummary:
        """The deterministic half of the CEO pre-session briefing (R10.2)."""
        now = self._clock.now()
        start, end = week_bounds(week_start) if week_start else previous_week(now.date())
        snapshots, as_of = await load_snapshots(s, user_id, start, end, now)
        overall = compute(snapshots, start, end, as_of)
        by_category = await self._by_category(s, user_id, start, end)
        wins, gaps = await self._wins_and_gaps(s, user_id, start, end)
        kept, broken = await self._commitment_counts(s, user_id, start, end)
        score, delta = await self._integrity_trend(s, user_id, start, end)
        return WeeklySummary(
            week_start=start,
            week_end=end,
            completion_rate=overall,
            by_category=by_category,
            wins=wins,
            gaps=gaps,
            integrity_score=score,
            integrity_delta=delta,
            kept_commitments=kept,
            broken_commitments=broken,
        )

    async def _actions(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date
    ) -> list[m.DailyAction]:
        return list(
            (
                await s.execute(
                    select(m.DailyAction).where(
                        m.DailyAction.user_id == user_id,
                        m.DailyAction.date >= start,
                        m.DailyAction.date <= end,
                        m.DailyAction.lifecycle_state == "active",
                    )
                )
            ).scalars()
        )

    async def _by_category(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date
    ) -> list[CategoryRate]:
        counts: dict[str, tuple[int, int]] = {}
        for action in await self._actions(s, user_id, start, end):
            category = await category_of_action(s, action) or UNLINKED
            completed, total = counts.get(category, (0, 0))
            counts[category] = (completed + (action.status == "Completed"), total + 1)
        return category_rates(counts)

    async def _wins_and_gaps(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date
    ) -> tuple[list[Win], list[Gap]]:
        completions: dict[tuple[str, uuid.UUID], int] = {}
        misses: dict[tuple[str, uuid.UUID], int] = {}
        titles: dict[tuple[str, uuid.UUID], str] = {}
        priorities: dict[tuple[str, uuid.UUID], int] = {}
        for action in await self._actions(s, user_id, start, end):
            key = (action.source_type, action.source_id or action.id)
            titles[key] = action.title
            if key not in priorities:
                priorities[key] = await self._priority_of_action(s, action)
            if action.status == "Completed":
                completions[key] = completions.get(key, 0) + 1
            else:  # Skipped, or still Planned/Started after the week closed
                misses[key] = misses.get(key, 0) + 1

        wins = [
            Win("task" if key[0] == "TASK" else "source", key[1], titles[key], count, priorities[key])
            for key, count in completions.items()
        ]
        gaps = [Gap("source", key[1], titles[key], count, priorities[key]) for key, count in misses.items()]
        wins += await self._commitment_wins(s, user_id, start, end)
        gaps += await self._commitment_gaps(s, user_id, start, end)
        objective_wins, objective_gaps = await self._objective_movement(s, user_id, start, end)
        return rank_wins(wins + objective_wins), rank_gaps(gaps + objective_gaps)

    async def _priority_of_action(self, s: AsyncSession, action: m.DailyAction) -> int:
        goal_id = await goal_of_source(s, action.source_type, action.source_id)
        if goal_id is None:
            return 0
        return (
            await s.execute(select(m.Goal.priority).where(m.Goal.id == goal_id))
        ).scalar_one_or_none() or 0

    async def _commitments(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date, status: str
    ) -> list[m.Commitment]:
        """Commitments resolved inside the local week (design §11.4 uses the same windowing)."""
        tz = await user_timezone(s, user_id)
        begins, _ = local_day_bounds(start, tz)
        _, ends = local_day_bounds(end, tz)
        stamp = m.Commitment.kept_at if status == "Kept" else m.Commitment.broken_at
        return list(
            (
                await s.execute(
                    select(m.Commitment).where(
                        m.Commitment.user_id == user_id,
                        m.Commitment.status == status,
                        stamp >= begins,
                        stamp < ends,
                    )
                )
            ).scalars()
        )

    async def _commitment_wins(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date
    ) -> list[Win]:
        return [
            Win("commitment", c.id, c.title, completions=1, goal_priority=await self._category_priority(s, c))
            for c in await self._commitments(s, user_id, start, end, "Kept")
        ]

    async def _commitment_gaps(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date
    ) -> list[Gap]:
        return [
            Gap("commitment", c.id, c.title, misses=1, goal_priority=await self._category_priority(s, c))
            for c in await self._commitments(s, user_id, start, end, "Broken")
        ]

    async def _category_priority(self, s: AsyncSession, commitment: m.Commitment) -> int:
        if commitment.goal_category is None:
            return 0
        return (
            await s.execute(
                select(m.Goal.priority)
                .where(
                    m.Goal.user_id == commitment.user_id,
                    m.Goal.category == commitment.goal_category,
                    m.Goal.deleted_at.is_(None),
                )
                .order_by(m.Goal.priority.desc())
                .limit(1)
            )
        ).scalar_one_or_none() or 0

    async def _objective_movement(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date
    ) -> tuple[list[Win], list[Gap]]:
        """Objectives that moved are wins; ones that did not, with a deadline inside 30 days, are gaps."""
        objectives = list(
            (
                await s.execute(
                    select(m.Objective).where(
                        m.Objective.user_id == user_id,
                        m.Objective.status == "active",
                        m.Objective.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        wins: list[Win] = []
        gaps: list[Gap] = []
        for objective in objectives:
            delta = await self._progress_delta(s, objective, start, end)
            priority = (
                await s.execute(select(m.Goal.priority).where(m.Goal.id == objective.goal_id))
            ).scalar_one_or_none() or 0
            if delta > 0:
                wins.append(Win("objective", objective.id, objective.title, 0, priority, delta))
            elif objective.target_date <= end + timedelta(days=OBJECTIVE_DEADLINE_DAYS):
                gaps.append(
                    Gap("objective", objective.id, objective.title, 0, priority, "no progress this week")
                )
        return wins, gaps

    async def _progress_delta(
        self, s: AsyncSession, objective: m.Objective, start: date, end: date
    ) -> Decimal:
        """Progress gained during the week, from the append-only value history."""
        rows = list(
            (
                await s.execute(
                    select(m.ObjectiveValueHistory.progress, m.ObjectiveValueHistory.changed_at)
                    .where(m.ObjectiveValueHistory.objective_id == objective.id)
                    .order_by(m.ObjectiveValueHistory.changed_at)
                )
            ).tuples()
        )
        before = [progress for progress, at in rows if at.date() < start]
        during = [progress for progress, at in rows if start <= at.date() <= end]
        if not during:
            return Decimal(0)
        baseline = before[-1] if before else Decimal(0)
        return Decimal(during[-1]) - Decimal(baseline)

    async def _commitment_counts(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date
    ) -> tuple[int, int]:
        return (
            len(await self._commitments(s, user_id, start, end, "Kept")),
            len(await self._commitments(s, user_id, start, end, "Broken")),
        )

    async def _integrity_trend(
        self, s: AsyncSession, user_id: uuid.UUID, start: date, end: date
    ) -> tuple[Decimal | None, Decimal | None]:
        """The integrity score at the end of the week against the score the week before (R10.2)."""
        latest = await self._snapshot_on_or_before(s, user_id, end)
        earlier = await self._snapshot_on_or_before(s, user_id, start - timedelta(days=1))
        if latest is None or earlier is None:
            return latest, None
        return latest, latest - earlier

    async def _snapshot_on_or_before(self, s: AsyncSession, user_id: uuid.UUID, day: date) -> Decimal | None:
        return (
            await s.execute(
                select(m.IntegrityScoreSnapshot.score)
                .where(
                    m.IntegrityScoreSnapshot.user_id == user_id,
                    m.IntegrityScoreSnapshot.local_date <= day,
                )
                .order_by(m.IntegrityScoreSnapshot.local_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
