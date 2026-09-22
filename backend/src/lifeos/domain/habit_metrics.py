"""Habit metrics (design §17.3, R1.11–1.12). Pure and deterministic.

Period: the local day for daily habits with target 1, otherwise the ISO week (Mon–Sun).
Per period: `required = min(frequency_target, scheduled non-paused days)`; periods requiring nothing
(fully paused / nothing scheduled) are EXCLUDED — they neither break nor extend a streak.
Only `completed` occurrences count toward targets (partials do not, §17.2); completions on unscheduled
days still count toward a weekly target. The in-progress current period extends a streak when already
met and never breaks it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from lifeos.domain.timeutil import iso_week_start, local_weekday_sunday0

PeriodKind = Literal["day", "week"]
Result = Literal["completed", "skipped", "partial"]


@dataclass(frozen=True)
class HabitDefinition:
    recurrence_type: str
    recurrence_days: list[int] | None
    frequency_target: int
    start: date
    end: date | None = None
    pauses: list[tuple[date, date | None]] = field(default_factory=list[tuple[date, date | None]])


@dataclass(frozen=True)
class PeriodStat:
    start: date
    end: date
    completed: int
    required: int
    in_progress: bool

    @property
    def excluded(self) -> bool:
        return self.required == 0

    @property
    def met(self) -> bool:
        return not self.excluded and self.completed >= self.required


@dataclass(frozen=True)
class HabitMetrics:
    period_kind: PeriodKind
    periods: list[PeriodStat]
    current_streak: int
    longest_streak: int
    missed: int
    partials: int
    total_completions: int


def period_kind(habit: HabitDefinition) -> PeriodKind:
    return "day" if habit.recurrence_type == "daily" and habit.frequency_target == 1 else "week"


def is_paused(habit: HabitDefinition, day: date) -> bool:
    return any(start <= day and (end is None or day <= end) for start, end in habit.pauses)


def is_scheduled(habit: HabitDefinition, day: date) -> bool:
    if day < habit.start or (habit.end is not None and day > habit.end) or is_paused(habit, day):
        return False
    return habit.recurrence_type == "daily" or local_weekday_sunday0(day) in (habit.recurrence_days or [])


def _periods(habit: HabitDefinition, as_of: date) -> list[tuple[date, date]]:
    kind = period_kind(habit)
    cursor = habit.start if kind == "day" else iso_week_start(habit.start)
    step = timedelta(days=1 if kind == "day" else 7)
    spans: list[tuple[date, date]] = []
    while cursor <= as_of:
        spans.append((cursor, cursor + step - timedelta(days=1)))
        cursor += step
    return spans


def compute_metrics(habit: HabitDefinition, records: Mapping[date, str], as_of: date) -> HabitMetrics:
    stats: list[PeriodStat] = []
    for start, end in _periods(habit, as_of):
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        scheduled = [d for d in days if is_scheduled(habit, d)]
        completed = sum(
            1 for d in days if d <= as_of and not is_paused(habit, d) and records.get(d) == "completed"
        )
        stats.append(
            PeriodStat(
                start, end, completed, min(habit.frequency_target, len(scheduled)), in_progress=end >= as_of
            )
        )

    current = 0
    for stat in reversed(stats):
        if stat.excluded or (stat.in_progress and not stat.met):
            continue  # excluded periods and an unfinished current period never break the streak
        if not stat.met:
            break
        current += 1

    longest = run = 0
    for stat in stats:
        if stat.excluded or (stat.in_progress and not stat.met):
            continue
        run = run + 1 if stat.met else 0
        longest = max(longest, run)

    missed = sum(
        1
        for stat in stats
        for i in range((stat.end - stat.start).days + 1)
        if (d := stat.start + timedelta(days=i)) < as_of
        and is_scheduled(habit, d)
        and records.get(d) not in ("completed", "partial")
    )
    return HabitMetrics(
        period_kind=period_kind(habit),
        periods=stats,
        current_streak=current,
        longest_streak=longest,
        missed=missed,
        partials=sum(1 for d, r in records.items() if r == "partial" and d <= as_of),
        total_completions=sum(s.completed for s in stats),
    )
