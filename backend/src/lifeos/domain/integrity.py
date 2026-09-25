"""Personal Integrity Score, daily snapshots and the threshold trigger (design §11.4–11.5, §16.6; R9.11–9.14).

    Score = Kept_30 / (Kept_30 + Broken_30 + OverdueDeferred_30) × 100, rounded half-up to 1 dp

The window is the last 30 local days including today, in the User's timezone. Cancelled Commitments
never count; a zero denominator gives `None` ("No resolved commitments in the last 30 days").
`refresh()` upserts today's snapshot and runs after every Commitment transition and at local midnight;
everything else (dashboard, agents) reads the stored snapshots (R24.3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.proactive import raise_flag, resolve_flags
from lifeos.domain.timeutil import local_date, local_day_bounds

WINDOW_DAYS = 30
TREND_DAYS = 30


@dataclass(frozen=True)
class IntegrityCounts:
    kept: int
    broken: int
    overdue_deferred: int

    @property
    def score(self) -> Decimal | None:
        return integrity_score(self.kept, self.broken, self.overdue_deferred)


def integrity_score(kept: int, broken: int, overdue_deferred: int) -> Decimal | None:
    denominator = kept + broken + overdue_deferred
    if denominator == 0:
        return None
    return (Decimal(kept) * 100 / Decimal(denominator)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def window_first_day(today: date) -> date:
    """First local date of the 30-day window ending (inclusive) on `today`."""
    return today - timedelta(days=WINDOW_DAYS - 1)


def crossed_below(previous: Decimal | None, current: Decimal | None, threshold: int) -> bool:
    """A downward crossing: from >= threshold (or no score) to < threshold (design §11.5)."""
    if current is None or current >= threshold:
        return False
    return previous is None or previous >= threshold


def crossing_dedupe_key(day: date) -> str:
    return f"integrity_below:{day.isoformat()}"


class IntegrityService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def compute(self, s: AsyncSession, user_id: uuid.UUID, today: date, tz: str) -> IntegrityCounts:
        first = window_first_day(today)
        start, _ = local_day_bounds(first, tz)
        _, end = local_day_bounds(today, tz)
        c = m.Commitment

        async def count(*conditions: Any) -> int:
            query = select(func.count()).select_from(c).where(c.user_id == user_id, *conditions)
            return int((await s.execute(query)).scalar_one())

        return IntegrityCounts(
            kept=await count(c.status == "Kept", c.kept_at >= start, c.kept_at < end),
            broken=await count(c.status == "Broken", c.broken_at >= start, c.broken_at < end),
            # currently Deferred, and the current deferred due date has passed and lies in the window
            overdue_deferred=await count(
                c.status == "Deferred", c.current_due_date < today, c.current_due_date >= first
            ),
        )

    async def refresh(self, s: AsyncSession, user_id: uuid.UUID) -> m.IntegrityScoreSnapshot:
        """Upsert today's snapshot and apply the threshold trigger (flag on a downward crossing)."""
        now = self._clock.now()
        user = (await s.execute(select(m.User).where(m.User.id == user_id))).scalar_one()
        today = local_date(now, user.timezone)
        previous = (
            await s.execute(
                select(m.IntegrityScoreSnapshot.score)
                .where(
                    m.IntegrityScoreSnapshot.user_id == user_id, m.IntegrityScoreSnapshot.local_date <= today
                )
                .order_by(m.IntegrityScoreSnapshot.local_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        counts = await self.compute(s, user_id, today, user.timezone)
        values = {
            "score": counts.score,
            "kept": counts.kept,
            "broken": counts.broken,
            "overdue_deferred": counts.overdue_deferred,
            "computed_at": now,
        }
        await s.execute(
            insert(m.IntegrityScoreSnapshot)
            .values(user_id=user_id, local_date=today, **values)
            .on_conflict_do_update(index_elements=["user_id", "local_date"], set_=values)
        )
        threshold = user.integrity_score_threshold
        if crossed_below(previous, counts.score, threshold):
            await raise_flag(
                s,
                user_id,
                "integrity_below_threshold",
                "accountability",
                crossing_dedupe_key(today),
                now,
                payload={"score": str(counts.score), "threshold": threshold, "previous": _str(previous)},
            )
        elif counts.score is not None and counts.score >= threshold:
            await resolve_flags(s, user_id, "integrity_below_threshold", now)
        return (
            await s.execute(
                select(m.IntegrityScoreSnapshot)
                .where(
                    m.IntegrityScoreSnapshot.user_id == user_id, m.IntegrityScoreSnapshot.local_date == today
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    async def overview(self, s: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
        """Current score, the 30-day series for the sparkline, and the trend vs. 30 days earlier."""
        current = await self.refresh(s, user_id)
        today = current.local_date
        baseline_day = today - timedelta(days=TREND_DAYS)
        rows = list(
            (
                await s.execute(
                    select(m.IntegrityScoreSnapshot)
                    .where(
                        m.IntegrityScoreSnapshot.user_id == user_id,
                        m.IntegrityScoreSnapshot.local_date >= baseline_day,
                        m.IntegrityScoreSnapshot.local_date <= today,
                    )
                    .order_by(m.IntegrityScoreSnapshot.local_date)
                )
            ).scalars()
        )
        baseline = next((r for r in rows if r.local_date == baseline_day), None)
        baseline_score = baseline.score if baseline is not None else None
        threshold = (
            await s.execute(select(m.User.integrity_score_threshold).where(m.User.id == user_id))
        ).scalar_one()
        return {
            "current": current,
            "threshold": threshold,
            "series": [r for r in rows if r.local_date > baseline_day],
            "baseline_date": baseline_day,
            "baseline_score": baseline_score,
            "delta": trend_delta(current.score, baseline_score),
        }


def trend_delta(current: Decimal | None, baseline: Decimal | None) -> Decimal | None:
    if current is None or baseline is None:
        return None
    return current - baseline


def _str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
