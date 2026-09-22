"""T5.3: which routine entries and habits apply on a local date, and when (design §12.1, §17.1)."""

import uuid
from datetime import UTC, date, datetime, time

from lifeos.domain.schedule.generation import (
    DayOverrides,
    EntrySpec,
    HabitSpec,
    TemplateSpec,
    habit_scheduled_on,
    plan_occurrences,
)

MON, TUE, SUN = date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 20)
WEEKDAYS = [1, 2, 3, 4, 5]


def tpl(days: list[int] = WEEKDAYS) -> TemplateSpec:
    return TemplateSpec(uuid.uuid4(), days)


def entry(
    t: TemplateSpec, start: time = time(9), end: time = time(10), habit: uuid.UUID | None = None
) -> EntrySpec:
    return EntrySpec(uuid.uuid4(), t.id, "Deep work", start, end, habit)


def habit(**kw: object) -> HabitSpec:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "title": "Run",
        "recurrence_type": "daily",
        "recurrence_days": None,
        "preferred_start": time(6, 30),
        "duration_minutes": 30,
        "start_date": None,
        "end_date": None,
        "created_on": date(2026, 1, 1),
    }
    base.update(kw)
    return HabitSpec(**base)  # type: ignore[arg-type]


def utc(h: int, mi: int = 0, d: date = MON) -> datetime:
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=UTC)


def test_routine_entries_apply_on_active_days_only() -> None:
    t = tpl()
    e = entry(t)
    assert [p.source_id for p in plan_occurrences(MON, "UTC", [t], [e], [], DayOverrides())] == [e.id]
    assert plan_occurrences(SUN, "UTC", [t], [e], [], DayOverrides()) == []


def test_routine_times_resolved_in_user_timezone() -> None:
    t = tpl()
    (p,) = plan_occurrences(MON, "Africa/Nairobi", [t], [entry(t)], [], DayOverrides())
    assert (p.start, p.end) == (utc(6), utc(7))  # 09:00–10:00 EAT
    assert p.template_id == t.id


def test_midnight_crossing_entry_ends_next_day() -> None:
    t = tpl()
    (p,) = plan_occurrences(MON, "UTC", [t], [entry(t, time(23), time(0, 30))], [], DayOverrides())
    assert (p.start, p.end) == (utc(23), utc(0, 30, TUE))


def test_removed_and_modified_routine_exceptions() -> None:
    t = tpl()
    a, b = entry(t), entry(t, time(11), time(12))
    moved = (utc(15), utc(16))
    overrides = DayOverrides(removed_entries=frozenset({a.id}), modified_entries={b.id: moved})
    (p,) = plan_occurrences(MON, "UTC", [t], [a, b], [], overrides)
    assert (p.source_id, p.start, p.end) == (b.id, *moved)


def test_daily_habit_uses_preferred_start_and_duration() -> None:
    h = habit()
    (p,) = plan_occurrences(MON, "UTC", [], [], [h], DayOverrides())
    assert (p.source_type, p.start, p.end) == ("HABIT", utc(6, 30), utc(7, 0))


def test_habit_defaults_to_wake_time_then_eight() -> None:
    h = habit(preferred_start=None, duration_minutes=None)
    (p,) = plan_occurrences(MON, "UTC", [], [], [h], DayOverrides(), wake_time=time(5, 45))
    assert (p.start, p.end) == (utc(5, 45), utc(6, 15))
    (q,) = plan_occurrences(MON, "UTC", [], [], [h], DayOverrides())
    assert q.start == utc(8)


def test_weekly_habit_only_on_listed_days() -> None:
    h = habit(recurrence_type="weekly", recurrence_days=[1, 3, 5])  # Mon/Wed/Fri
    assert habit_scheduled_on(h, MON)
    assert not habit_scheduled_on(h, TUE)


def test_habit_date_window_and_creation_day() -> None:
    h = habit(start_date=TUE, end_date=date(2026, 9, 30))
    assert not habit_scheduled_on(h, MON)
    assert habit_scheduled_on(h, TUE)
    assert not habit_scheduled_on(h, date(2026, 10, 1))
    new = habit(created_on=TUE)
    assert not habit_scheduled_on(new, MON)  # no occurrences before the habit existed


def test_paused_days_are_skipped() -> None:
    open_pause = habit(pauses=[(MON, None)])
    closed_pause = habit(pauses=[(SUN, MON)])
    assert not habit_scheduled_on(open_pause, TUE)
    assert not habit_scheduled_on(closed_pause, MON)
    assert habit_scheduled_on(closed_pause, TUE)


def test_blocked_habit_never_scheduled() -> None:
    assert not habit_scheduled_on(habit(blocked=True), MON)


def test_routine_linked_habit_not_duplicated() -> None:
    h = habit()
    t = tpl()
    e = entry(t, habit=h.id)
    plan = plan_occurrences(MON, "UTC", [t], [e], [h], DayOverrides())
    assert [p.source_type for p in plan] == ["ROUTINE_ENTRY"]
    # On a day the routine does not run, the habit is generated on its own.
    assert [p.source_type for p in plan_occurrences(SUN, "UTC", [t], [e], [h], DayOverrides())] == ["HABIT"]


def test_habit_overrides() -> None:
    h, g = habit(), habit()
    moved = (utc(18), utc(18, 30))
    overrides = DayOverrides(removed_habits=frozenset({h.id}), rescheduled_habits={g.id: moved})
    (p,) = plan_occurrences(MON, "UTC", [], [], [h, g], overrides)
    assert (p.source_id, p.start, p.end) == (g.id, *moved)


def test_dst_day_times() -> None:
    t = tpl([0])  # Sunday 2026-03-08, New York springs forward
    (p,) = plan_occurrences(
        date(2026, 3, 8), "America/New_York", [t], [entry(t, time(2, 30), time(3, 30))], [], DayOverrides()
    )
    assert p.start == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 02:30 does not exist → 03:00 EDT
    assert p.end == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
