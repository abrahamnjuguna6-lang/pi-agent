"""T5.9: habit streaks, weekly targets, partials, pauses, missed occurrences (design §17.3, R1.11–1.12)."""

from datetime import date, timedelta

from lifeos.domain.habit_metrics import HabitDefinition, compute_metrics, is_scheduled, period_kind

MON = date(2026, 9, 7)  # a Monday


def day(n: int) -> date:
    return MON + timedelta(days=n)


def daily(**kw: object) -> HabitDefinition:
    return HabitDefinition(
        **{"recurrence_type": "daily", "recurrence_days": None, "frequency_target": 1, "start": MON, **kw}
    )  # type: ignore[arg-type]


def test_daily_streak_counts_consecutive_days() -> None:
    records = {day(i): "completed" for i in range(5)}  # Mon..Fri
    m = compute_metrics(daily(), records, as_of=day(4))
    assert (m.period_kind, m.current_streak, m.longest_streak, m.missed) == ("day", 5, 5, 0)


def test_unfinished_today_does_not_break_streak() -> None:
    records = {day(i): "completed" for i in range(4)}  # nothing yet today (day 4)
    m = compute_metrics(daily(), records, as_of=day(4))
    assert m.current_streak == 4
    assert m.missed == 0  # today is not missed yet


def test_gap_resets_current_but_keeps_longest() -> None:
    records = {day(0): "completed", day(1): "completed", day(2): "completed", day(4): "completed"}
    m = compute_metrics(daily(), records, as_of=day(4))
    assert (m.current_streak, m.longest_streak, m.missed) == (1, 3, 1)


def test_weekly_target_three_per_week() -> None:
    habit = HabitDefinition("weekly", [1, 3, 5], 3, MON)  # Mon/Wed/Fri
    week1 = {day(0): "completed", day(2): "completed", day(4): "completed"}
    week2 = {day(7): "completed", day(9): "completed"}  # only 2 of 3
    on_sunday = compute_metrics(habit, week1 | week2, as_of=day(13))  # week 2 still in progress today
    assert on_sunday.current_streak == 1  # an unfinished week never breaks the streak
    m = compute_metrics(habit, week1 | week2, as_of=day(14))  # Monday: week 2 has ended unmet
    assert m.period_kind == "week"
    assert [(p.completed, p.required, p.met) for p in m.periods[:2]] == [(3, 3, True), (2, 3, False)]
    assert (m.current_streak, m.longest_streak) == (0, 1)
    assert m.missed == 1  # Friday of week 2


def test_completion_on_unscheduled_day_counts_toward_weekly_target() -> None:
    habit = HabitDefinition("custom", [1, 3, 5], 2, MON)
    m = compute_metrics(habit, {day(1): "completed", day(5): "completed"}, as_of=day(6))  # Tue + Sat
    assert m.periods[0].met


def test_partial_does_not_count_toward_target() -> None:
    m = compute_metrics(daily(), {day(0): "completed", day(1): "partial"}, as_of=day(2))
    assert (m.current_streak, m.partials) == (0, 1)
    assert m.missed == 0  # a partial is not a miss (§17.2)


def test_pause_days_neither_break_nor_extend_streak() -> None:
    habit = daily(pauses=[(day(2), day(3))])
    records = {day(0): "completed", day(1): "completed", day(4): "completed"}
    m = compute_metrics(habit, records, as_of=day(4))
    assert (m.current_streak, m.longest_streak, m.missed) == (3, 3, 0)
    assert [p.excluded for p in m.periods] == [False, False, True, True, False]


def test_completion_during_pause_is_ignored() -> None:
    m = compute_metrics(daily(pauses=[(day(1), None)]), {day(1): "completed"}, as_of=day(1))
    assert m.total_completions == 0


def test_weekly_period_partially_paused_scales_requirement() -> None:
    habit = HabitDefinition("weekly", [1, 3, 5], 3, MON, pauses=[(day(2), day(6))])  # only Monday remains
    m = compute_metrics(habit, {day(0): "completed"}, as_of=day(6))
    assert (m.periods[0].required, m.periods[0].met) == (1, True)


def test_skips_count_as_missed_and_end_date_respected() -> None:
    habit = daily(end=day(2))
    m = compute_metrics(habit, {day(0): "skipped"}, as_of=day(5))
    assert m.missed == 3  # days 0 (skipped), 1, 2 (no record); nothing after the end date
    assert not is_scheduled(habit, day(3))
    assert not is_scheduled(habit, MON - timedelta(days=1))


def test_period_kind() -> None:
    assert period_kind(daily()) == "day"
    assert period_kind(daily(frequency_target=5)) == "week"
    assert period_kind(HabitDefinition("weekly", [1], 1, MON)) == "week"
