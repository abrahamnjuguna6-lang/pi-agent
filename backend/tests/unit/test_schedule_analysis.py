"""T8.2: conflicts, waking hours and overload for a local day (design §16.8; R6.4)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from lifeos.domain.schedule.analysis import (
    Block,
    analyze,
    booked_minutes,
    conflicts,
    day_is_overloaded,
    outside_waking_hours,
    overloaded_windows,
    waking_minutes,
)

DAY = date(2026, 9, 21)
TZ = "UTC"
WAKE, SLEEP = time(6, 0), time(22, 0)  # a 960-minute waking window


def block(title: str, start_hour: float, minutes: int = 60) -> Block:
    start = datetime(2026, 9, 21, tzinfo=UTC) + timedelta(hours=start_hour)
    return Block(uuid.uuid5(uuid.NAMESPACE_OID, title), title, start, start + timedelta(minutes=minutes))


def test_overlapping_actions_are_a_conflict() -> None:  # R6.4
    first, second = block("Standup", 9), block("Client call", 9.5)
    (conflict,) = conflicts([second, first])
    assert (conflict.first.title, conflict.second.title) == ("Standup", "Client call")
    assert conflict.overlap_minutes == 30


def test_touching_blocks_do_not_conflict() -> None:
    assert conflicts([block("A", 9), block("B", 10)]) == []


def test_every_overlapping_pair_is_reported() -> None:
    three = [block("A", 9, 180), block("B", 9.5), block("C", 10)]
    assert len(conflicts(three)) == 3


def test_actions_outside_waking_hours() -> None:
    early, late, fine = block("Gym", 5), block("Email", 22.5), block("Lunch", 12)
    found = outside_waking_hours([early, late, fine], DAY, WAKE, SLEEP, TZ)
    assert {b.title for b in found} == {"Gym", "Email"}
    assert outside_waking_hours([early], DAY, None, None, TZ) == []  # unknown hours → no finding


def test_a_late_sleep_time_belongs_to_the_next_day() -> None:
    """A 23:00 sleep time with a 07:00 wake time means the night rolls over midnight."""
    late_night = block("Reading", 23.5)
    assert outside_waking_hours([late_night], DAY, time(7, 0), time(23, 0), TZ) == [late_night]
    assert outside_waking_hours([late_night], DAY, time(7, 0), time(1, 0), TZ) == []


def test_booked_minutes_counts_overlapping_time_once() -> None:
    assert booked_minutes([block("A", 9), block("B", 9.5)]) == 90
    assert booked_minutes([]) == 0


def test_a_solid_three_hour_window_is_overloaded() -> None:  # design §16.8
    back_to_back = [block("A", 9), block("B", 10), block("C", 11)]
    (window,) = overloaded_windows(back_to_back)
    assert window.start == back_to_back[0].start
    assert window.largest_gap_minutes == 0


def test_a_ten_minute_gap_saves_the_window() -> None:
    with_break = [block("A", 9, 50), block("B", 10), block("C", 11)]
    assert overloaded_windows(with_break) == []


def test_a_short_gap_does_not_save_the_window() -> None:
    barely = [block("A", 9, 55), block("B", 10), block("C", 11)]
    assert len(overloaded_windows(barely)) == 1


def test_a_light_day_has_no_overloaded_window() -> None:
    assert overloaded_windows([block("A", 9), block("B", 14)]) == []


def test_the_day_is_overloaded_above_ninety_percent_of_waking_minutes() -> None:
    assert waking_minutes(DAY, WAKE, SLEEP, TZ) == 960
    assert day_is_overloaded(865, 960) is True  # 90.1%
    assert day_is_overloaded(864, 960) is False  # exactly 90%
    assert day_is_overloaded(900, None) is False  # unknown waking window
    assert day_is_overloaded(10, 0) is False


def test_analyze_collects_every_finding() -> None:
    blocks = [block("A", 9), block("B", 9.5), block("Night owl", 23)]
    analysis = analyze(DAY, blocks, WAKE, SLEEP, TZ)
    assert analysis.has_findings is True
    assert len(analysis.conflicts) == 1
    assert [b.title for b in analysis.outside_waking] == ["Night owl"]
    assert analysis.booked_minutes == 150
    assert analysis.waking_minutes == 960
    assert analysis.day_overloaded is False
    data = analysis.as_json()
    assert data["date"] == "2026-09-21"
    assert data["conflicts"][0]["overlap_minutes"] == 30


def test_an_empty_day_has_no_findings() -> None:
    analysis = analyze(DAY, [], WAKE, SLEEP, TZ)
    assert analysis.has_findings is False
    assert (analysis.booked_minutes, analysis.day_overloaded) == (0, False)
