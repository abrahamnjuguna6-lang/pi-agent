"""Completion Rate (design §16.3, R3.12–3.13).

    Completion Rate = Completed / (Planned + Started + Skipped + Completed)

over non-cancelled Daily Actions whose scheduled local `date` is in the period. Each action's status is
taken from the Check-in history (authoritative) as of the evaluation instant = min(now, period end).
Cancelled actions are excluded; an action past its end with no update counts as Planned (incomplete);
a period with no scheduled actions has no rate (`None`), not 0%.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.schedule.common import user_timezone
from lifeos.domain.timeutil import local_day_bounds

COUNTED = ("Planned", "Started", "Skipped", "Completed")


@dataclass(frozen=True)
class ActionSnapshot:
    date: date
    created_at: datetime
    cancelled_at: datetime | None
    checkins: Sequence[
        tuple[datetime, str | None, str]
    ]  # (created_at, previous_status, new_status), any order


@dataclass(frozen=True)
class CompletionRate:
    rate: Decimal | None  # 0..1, 4 dp; None when nothing was scheduled
    completed: int
    planned: int
    started: int
    skipped: int

    @property
    def total(self) -> int:
        return self.completed + self.planned + self.started + self.skipped

    @property
    def percent(self) -> Decimal | None:
        return (
            None if self.rate is None else (self.rate * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )


def status_as_of(checkins: Sequence[tuple[datetime, str | None, str]], as_of: datetime) -> str:
    """Latest check-in at or before `as_of`; an action with none yet is Planned.

    Records sharing the latest timestamp (e.g. created and completed within one instant) are ordered by
    the transition chain: the latest is the one whose `new_status` no other tied record transitions FROM.
    """
    visible = [c for c in checkins if c[0] <= as_of]
    if not visible:
        return "Planned"
    latest_at = max(c[0] for c in visible)
    tied = [c for c in visible if c[0] == latest_at]
    sources = {c[1] for c in tied}
    heads = [c for c in tied if c[2] not in sources]
    return (heads or tied)[-1][2]


def compute(snapshots: Sequence[ActionSnapshot], start: date, end: date, as_of: datetime) -> CompletionRate:
    counts: dict[str, int] = dict.fromkeys(COUNTED, 0)
    for snap in snapshots:
        if not start <= snap.date <= end:
            continue
        if snap.created_at > as_of:
            continue  # did not exist yet at evaluation time
        if snap.cancelled_at is not None and snap.cancelled_at <= as_of:
            continue  # cancelled actions are excluded (R3.12)
        counts[status_as_of(snap.checkins, as_of)] += 1
    total = sum(counts.values())
    rate = (
        None
        if total == 0
        else (Decimal(counts["Completed"]) / Decimal(total)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    )
    return CompletionRate(rate, counts["Completed"], counts["Planned"], counts["Started"], counts["Skipped"])


async def completion_rate(
    s: AsyncSession, user_id: uuid.UUID, start: date, end: date, now: datetime
) -> CompletionRate:
    """Completion Rate for the User-local period [start, end] (inclusive).

    Evaluated as of min(now, period end)."""
    tz = await user_timezone(s, user_id)
    period_end = local_day_bounds(end, tz)[1]
    as_of = min(now, period_end)
    actions = list(
        (
            await s.execute(
                select(m.DailyAction).where(
                    m.DailyAction.user_id == user_id, m.DailyAction.date >= start, m.DailyAction.date <= end
                )
            )
        ).scalars()
    )
    history: dict[uuid.UUID, list[tuple[datetime, str | None, str]]] = {a.id: [] for a in actions}
    if actions:
        for action_id, at, previous, status in (
            await s.execute(
                select(
                    m.CheckinRecord.daily_action_id,
                    m.CheckinRecord.created_at,
                    m.CheckinRecord.previous_status,
                    m.CheckinRecord.new_status,
                ).where(m.CheckinRecord.daily_action_id.in_(list(history)))
            )
        ).tuples():
            history[action_id].append((at, previous, status))
    snapshots = [ActionSnapshot(a.date, a.created_at, a.cancelled_at, history[a.id]) for a in actions]
    return compute(snapshots, start, end, as_of)
