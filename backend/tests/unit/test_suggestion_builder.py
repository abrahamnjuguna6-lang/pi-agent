"""T5.10: late-completion proposal builder (design §19.9, R2.9)."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

from lifeos.domain.schedule.suggestions import Following, build_proposal, sleep_limit_for


def at(h: int, mi: int = 0, d: int = 21) -> datetime:
    return datetime(2026, 9, d, h, mi, tzinfo=UTC)


def item(start: datetime, minutes: int) -> Following:
    return Following(uuid.uuid4(), start, start + timedelta(minutes=minutes))


def test_shifts_preserve_order_and_durations() -> None:
    a, b = item(at(15), 60), item(at(17), 30)
    shifts = build_proposal(timedelta(minutes=40), [b, a], sleep_limit=None)
    assert [s.action_id for s in shifts] == [a.action_id, b.action_id]
    assert (shifts[0].new_start, shifts[0].new_end) == (at(15, 40), at(16, 40))
    assert (shifts[1].new_start, shifts[1].new_end) == (at(17, 40), at(18, 10))
    assert not any(s.move_to_tomorrow for s in shifts)


def test_past_sleep_time_moves_to_tomorrow_same_time() -> None:
    late = item(at(21, 30), 60)  # 21:30–22:30; +45 min would end 23:15 > 22:30 sleep
    (shift,) = build_proposal(timedelta(minutes=45), [late], sleep_limit=at(22, 30))
    assert shift.move_to_tomorrow
    assert (shift.new_start, shift.new_end) == (at(21, 30, 22), at(22, 30, 22))


def test_ending_exactly_at_sleep_time_stays_today() -> None:
    (shift,) = build_proposal(timedelta(minutes=30), [item(at(21), 60)], sleep_limit=at(22, 30))
    assert not shift.move_to_tomorrow


def test_sleep_limit_after_midnight() -> None:
    assert sleep_limit_for(date(2026, 9, 21), time(23, 0), time(6, 0), "UTC") == at(23)
    assert sleep_limit_for(date(2026, 9, 21), time(1, 0), time(9, 0), "UTC") == at(1, 0, 22)
    assert sleep_limit_for(date(2026, 9, 21), None, None, "UTC") is None
    assert sleep_limit_for(date(2026, 9, 21), time(11, 0), None, "UTC") == at(11, 0, 22)  # before noon pivot
