"""T6.4: escalation engine (design §14.2–14.4, §38.1) — 100% branch coverage of the pure logic."""

from datetime import UTC, date, datetime, timedelta

import pytest

from lifeos.domain.accountability import (
    BASELINE,
    Outcome,
    SourceState,
    day_outcomes,
    evaluate,
    flag_dedupe_key,
    in_pause,
    occurrence_level,
    pattern_level,
    recovery_dates,
    trailing_skip_run,
)

TODAY = date(2026, 9, 21)  # a Monday


def days(*offsets: int) -> list[date]:
    return [TODAY - timedelta(days=o) for o in offsets]


def marks(status: Outcome, *offsets: int) -> dict[date, Outcome]:
    """`marks("Skipped", 0, 1)` — that outcome on today and yesterday."""
    return {TODAY - timedelta(days=offset): status for offset in offsets}


def outcomes(**by_offset: str) -> dict[date, Outcome]:
    """`outcomes(d0="Skipped", d3="Completed")` — keys are days before today."""
    return {
        TODAY - timedelta(days=int(key[1:])): value  # type: ignore[misc]
        for key, value in by_offset.items()
    }


# --------------------------------------------------------------------------- Levels 1–2 (occurrence)


@pytest.mark.parametrize(
    ("status", "lifecycle", "minutes", "expected"),
    [
        ("Planned", "active", -10, 0),  # before the scheduled start
        ("Planned", "active", 0, 1),  # R8.2: reached the start, still Planned
        ("Planned", "active", 30, 1),
        ("Planned", "active", 60, 2),  # R8.3: passed the end
        ("Started", "active", 30, 0),  # started and still inside the block
        ("Started", "active", 60, 2),
        ("Completed", "active", 60, 0),
        ("Skipped", "active", 60, 0),
        ("Planned", "cancelled", 60, 0),
    ],
)
def test_occurrence_level(status: str, lifecycle: str, minutes: int, expected: int) -> None:
    start = datetime(2026, 9, 21, 7, 0, tzinfo=UTC)
    end = start + timedelta(minutes=60)
    assert occurrence_level(status, lifecycle, start, end, start + timedelta(minutes=minutes)) == expected


# --------------------------------------------------------------------------- outcomes and pauses


def test_pause_days_are_excluded() -> None:  # R1.12
    pauses = [(TODAY - timedelta(days=4), TODAY - timedelta(days=2))]
    assert in_pause(TODAY - timedelta(days=3), pauses) is True
    assert in_pause(TODAY - timedelta(days=1), pauses) is False
    assert in_pause(TODAY, [(TODAY - timedelta(days=1), None)]) is True  # open-ended pause
    occurrences = [(day, "Skipped") for day in days(0, 1, 2, 3, 4)]
    assert set(day_outcomes(occurrences, pauses)) == set(days(0, 1))


def test_completed_beats_skipped_on_the_same_date() -> None:
    day = TODAY
    assert day_outcomes([(day, "Skipped"), (day, "Completed")], []) == {day: "Completed"}
    assert day_outcomes([(day, "Completed"), (day, "Skipped")], []) == {day: "Completed"}
    assert day_outcomes([(day, "Planned"), (day, "Skipped")], []) == {day: "Skipped"}
    assert day_outcomes([(day, "Planned"), (day, "Started")], []) == {day: "Open"}


def test_trailing_run_counts_only_resolved_occurrences() -> None:
    assert trailing_skip_run({}) == (0, None)
    unresolved_today = outcomes(d0="Open", d1="Skipped", d2="Skipped")
    assert trailing_skip_run(unresolved_today) == (2, TODAY - timedelta(days=1))
    assert trailing_skip_run(outcomes(d0="Completed", d1="Skipped")) == (0, None)


# --------------------------------------------------------------------------- Levels 3–5 (source pattern)


@pytest.mark.parametrize(
    ("skips", "expected"),
    [(0, 1), (1, 1), (2, 1), (3, 3), (4, 3), (5, 5)],  # 5 skips in a row are also 5 consecutive occurrences
)
def test_pattern_level_by_skip_count(skips: int, expected: int) -> None:
    assert pattern_level(marks("Skipped", *range(skips)), TODAY, None) == expected


def test_five_skips_in_seven_days_without_a_run_is_level_4() -> None:  # R8.5
    state = outcomes(d0="Skipped", d1="Skipped", d2="Completed", d3="Skipped", d4="Skipped", d5="Skipped")
    assert pattern_level(state, TODAY, None) == 4


def test_sparse_source_reaches_level_5_on_consecutive_occurrences(  # Mon/Wed/Fri habit (design §14.2)
) -> None:
    state = marks("Skipped", 0, 2, 4, 7, 9)  # five scheduled occurrences, all skipped
    assert pattern_level(state, TODAY, None) == 5  # only 3 fall in the last 7 days, but the run is 5


def test_a_stale_run_does_not_escalate() -> None:
    state = marks("Skipped", 20, 22, 24, 26, 28)
    assert pattern_level(state, TODAY, None) == 1  # the latest skip is outside the 7-day window


def test_future_days_are_ignored() -> None:
    tomorrow: dict[date, Outcome] = {TODAY + timedelta(days=1): "Skipped"}
    state = tomorrow | marks("Skipped", 0, 1)
    assert pattern_level(state, TODAY, None) == 1


def test_skips_before_the_recovery_anchor_do_not_re_escalate() -> None:  # design §14.4
    state = marks("Skipped", 4, 5, 6)
    assert pattern_level(state, TODAY, None) == 3
    assert pattern_level(state, TODAY, TODAY - timedelta(days=4)) == 1


def test_recovery_dates_are_windowed_and_after_the_anchor() -> None:
    state = marks("Completed", 0, 2, 4, 8) | outcomes(d1="Skipped")
    assert recovery_dates(state, TODAY, None) == sorted(days(4, 2, 0))  # the day-8 one is out of window
    assert recovery_dates(state, TODAY, TODAY - timedelta(days=3)) == sorted(days(2, 0))


# --------------------------------------------------------------------------- state machine (§38.1 table)


def test_no_pattern_keeps_the_source_at_baseline() -> None:
    assert evaluate(BASELINE, outcomes(d0="Skipped", d1="Skipped"), TODAY) == (evaluate(BASELINE, {}, TODAY))
    assert evaluate(BASELINE, {}, TODAY).level == 1


def test_level_3_starts_an_episode() -> None:
    decision = evaluate(BASELINE, marks("Skipped", 0, 1, 2), TODAY)
    assert (decision.level, decision.reflection_required, decision.new_episode) == (3, False, True)


def test_level_4_requires_a_reflection() -> None:  # R8.5
    skips = outcomes(d0="Skipped", d1="Skipped", d2="Completed", d3="Skipped", d4="Skipped", d5="Skipped")
    decision = evaluate(SourceState(3, False, False, None), skips, TODAY)
    assert (decision.level, decision.reflection_required, decision.new_episode) == (4, True, False)


def test_level_5_keeps_the_gate_without_a_new_reflection() -> None:  # design §14.3
    state = SourceState(4, False, True, None)  # reflection already submitted in this episode
    decision = evaluate(state, marks("Skipped", 0, 1, 2, 3, 4), TODAY)
    assert (decision.level, decision.reflection_required) == (5, False)


def test_level_5_requires_a_reflection_when_none_was_submitted() -> None:
    skips = marks("Skipped", 0, 1, 2, 3, 4)
    decision = evaluate(SourceState(3, False, False, None), skips, TODAY)
    assert (decision.level, decision.reflection_required) == (5, True)


def test_a_new_episode_resets_the_reflection() -> None:
    state = SourceState(1, False, True, TODAY - timedelta(days=30))  # reflected in a previous episode
    decision = evaluate(state, marks("Skipped", 0, 1, 2, 3, 4), TODAY)
    assert (decision.level, decision.reflection_required, decision.new_episode) == (5, True, True)


def test_no_reduction_while_a_reflection_is_pending() -> None:  # design §14.3
    state = SourceState(4, True, False, None)
    completions = marks("Completed", 0, 1, 2)
    decision = evaluate(state, completions, TODAY)
    assert (decision.level, decision.reflection_required, decision.reduced) == (4, True, False)


def test_three_completions_reduce_by_one_level_and_set_the_anchor() -> None:  # R8.10
    state = SourceState(4, False, True, None)
    decision = evaluate(state, marks("Completed", 0, 1, 2), TODAY)
    assert (decision.level, decision.reduced, decision.anchor) == (3, True, TODAY)


def test_the_same_completions_do_not_reduce_twice() -> None:  # design §14.4
    completions = marks("Completed", 0, 1, 2)
    first = evaluate(SourceState(4, False, True, None), completions, TODAY)
    second = evaluate(SourceState(first.level, False, True, first.anchor), completions, TODAY)
    assert (second.level, second.reduced) == (3, False)


def test_two_completions_are_not_enough() -> None:
    decision = evaluate(SourceState(3, False, False, None), marks("Completed", 0, 1), TODAY)
    assert (decision.level, decision.reduced) == (3, False)


def test_reduction_stops_at_level_1() -> None:  # R8.10: never below Level 1
    state = SourceState(2, False, False, None)
    decision = evaluate(state, marks("Completed", 0, 1, 2), TODAY)
    assert decision.level == 1
    assert evaluate(SourceState(1, False, False, decision.anchor), {}, TODAY).level == 1


def test_evaluation_is_idempotent() -> None:
    state = SourceState(3, False, False, None)
    skips = marks("Skipped", 0, 1, 2)
    first = evaluate(state, skips, TODAY)
    again = evaluate(SourceState(first.level, first.reflection_required, False, first.anchor), skips, TODAY)
    assert (first.level, again.level, again.reduced, again.new_episode) == (3, 3, False, False)


def test_flag_dedupe_key_is_per_episode_and_level() -> None:
    import uuid

    episode = uuid.uuid4()
    assert flag_dedupe_key(episode, 3) == f"escalation:{episode}:3"
    assert flag_dedupe_key(episode, 3) != flag_dedupe_key(episode, 4)
