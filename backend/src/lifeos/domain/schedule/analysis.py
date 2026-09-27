"""Schedule conflicts and overload for a local day (design §16.8; R6.4).

Deterministic findings for the Daily Briefing and the Personal Assistant context:
    conflict         — two non-cancelled Daily Actions whose [start, end) intervals overlap
    outside_waking   — an action starting before wake time or ending after sleep time
    overloaded block — a rolling 3-hour window with no free gap of 10 minutes or more
    overloaded day   — booked minutes above 90% of the waking window
The agent explains these; it never decides them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.timeutil import local_date, resolve_local

WINDOW = timedelta(hours=3)
MIN_GAP = timedelta(minutes=10)
DAY_LOAD_RATIO = 0.9


@dataclass(frozen=True)
class Block:
    id: uuid.UUID
    title: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Conflict:
    first: Block
    second: Block

    @property
    def overlap_minutes(self) -> int:
        overlap = min(self.first.end, self.second.end) - max(self.first.start, self.second.start)
        return int(overlap.total_seconds() // 60)


@dataclass(frozen=True)
class OverloadedWindow:
    start: datetime
    end: datetime
    largest_gap_minutes: int


@dataclass(frozen=True)
class DayAnalysis:
    day: date
    conflicts: list[Conflict]
    outside_waking: list[Block]
    overloaded_windows: list[OverloadedWindow]
    day_overloaded: bool
    booked_minutes: int
    waking_minutes: int | None

    @property
    def has_findings(self) -> bool:
        return bool(self.conflicts or self.outside_waking or self.overloaded_windows or self.day_overloaded)

    def as_json(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "conflicts": [
                {
                    "first": {"id": str(c.first.id), "title": c.first.title},
                    "second": {"id": str(c.second.id), "title": c.second.title},
                    "overlap_minutes": c.overlap_minutes,
                }
                for c in self.conflicts
            ],
            "outside_waking": [{"id": str(b.id), "title": b.title} for b in self.outside_waking],
            "overloaded_windows": [
                {
                    "start": w.start.isoformat(),
                    "end": w.end.isoformat(),
                    "largest_gap_minutes": w.largest_gap_minutes,
                }
                for w in self.overloaded_windows
            ],
            "day_overloaded": self.day_overloaded,
            "booked_minutes": self.booked_minutes,
            "waking_minutes": self.waking_minutes,
        }


# --------------------------------------------------------------------------- pure rules


def conflicts(blocks: Sequence[Block]) -> list[Conflict]:
    """Every overlapping pair, earliest first. Touching blocks (end == start) do not overlap."""
    ordered = sorted(blocks, key=lambda b: (b.start, b.end, str(b.id)))
    found: list[Conflict] = []
    for index, block in enumerate(ordered):
        for other in ordered[index + 1 :]:
            if other.start >= block.end:
                break  # later blocks start even later
            found.append(Conflict(block, other))
    return found


def outside_waking_hours(
    blocks: Sequence[Block], day: date, wake: time | None, sleep: time | None, tz: str
) -> list[Block]:
    """Blocks that start before wake time or end after sleep time. A sleep time at or before wake
    time belongs to the next local day (a 23:00–07:00 night)."""
    if wake is None or sleep is None:
        return []
    wake_at = resolve_local(day, wake, tz)
    sleep_at = resolve_local(day + timedelta(days=1) if sleep <= wake else day, sleep, tz)
    return [block for block in blocks if block.start < wake_at or block.end > sleep_at]


def merge(blocks: Sequence[Block]) -> list[tuple[datetime, datetime]]:
    """Busy intervals with overlaps merged, so double-booked minutes are counted once."""
    merged: list[tuple[datetime, datetime]] = []
    for block in sorted(blocks, key=lambda b: (b.start, b.end)):
        if merged and block.start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], block.end))
        else:
            merged.append((block.start, block.end))
    return merged


def booked_minutes(blocks: Sequence[Block]) -> int:
    return int(sum((end - start).total_seconds() for start, end in merge(blocks)) // 60)


def largest_gap(busy: Sequence[tuple[datetime, datetime]], start: datetime, end: datetime) -> timedelta:
    """The longest free stretch inside [start, end)."""
    gap = timedelta()
    cursor = start
    for busy_start, busy_end in busy:
        if busy_end <= start or busy_start >= end:
            continue
        gap = max(gap, min(busy_start, end) - cursor)
        cursor = max(cursor, min(busy_end, end))
    return max(gap, end - cursor)


def overloaded_windows(blocks: Sequence[Block]) -> list[OverloadedWindow]:
    """Rolling 3-hour windows with no free gap of 10 minutes or more (design §16.8).

    Windows are anchored at each block's start: if any 3-hour stretch is that solid, one anchored at a
    block start is too, and the result reads back to the User as "from 09:00 you have no break"."""
    busy = merge(blocks)
    found: list[OverloadedWindow] = []
    for block in sorted(blocks, key=lambda b: b.start):
        start, end = block.start, block.start + WINDOW
        gap = largest_gap(busy, start, end)
        if gap < MIN_GAP and not any(window.start == start for window in found):
            found.append(OverloadedWindow(start, end, int(gap.total_seconds() // 60)))
    return found


def day_is_overloaded(booked: int, waking: int | None) -> bool:
    """Booked minutes above 90% of the waking window (design §16.8)."""
    return waking is not None and waking > 0 and booked > waking * DAY_LOAD_RATIO


def waking_minutes(day: date, wake: time | None, sleep: time | None, tz: str) -> int | None:
    if wake is None or sleep is None:
        return None
    wake_at = resolve_local(day, wake, tz)
    sleep_at = resolve_local(day + timedelta(days=1) if sleep <= wake else day, sleep, tz)
    return int((sleep_at - wake_at).total_seconds() // 60)


def analyze(
    day: date, blocks: Sequence[Block], wake: time | None, sleep: time | None, tz: str
) -> DayAnalysis:
    booked = booked_minutes(blocks)
    waking = waking_minutes(day, wake, sleep, tz)
    return DayAnalysis(
        day=day,
        conflicts=conflicts(blocks),
        outside_waking=outside_waking_hours(blocks, day, wake, sleep, tz),
        overloaded_windows=overloaded_windows(blocks),
        day_overloaded=day_is_overloaded(booked, waking),
        booked_minutes=booked,
        waking_minutes=waking,
    )


# --------------------------------------------------------------------------- service


class ScheduleAnalysisService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def analyze_day(self, s: AsyncSession, user_id: uuid.UUID, day: date | None = None) -> DayAnalysis:
        user = (await s.execute(select(m.User).where(m.User.id == user_id))).scalar_one()
        tz = user.timezone
        target = day or local_date(self._clock.now(), tz)
        blocks = [
            Block(action.id, action.title, action.scheduled_start, action.scheduled_end)
            for action in (
                await s.execute(
                    select(m.DailyAction).where(
                        m.DailyAction.user_id == user_id,
                        m.DailyAction.date == target,
                        m.DailyAction.lifecycle_state == "active",
                    )
                )
            ).scalars()
        ]
        return analyze(target, blocks, user.wake_time, user.sleep_time, tz)
