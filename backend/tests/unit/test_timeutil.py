"""T3.4: timezone/DST policy (design §20.3, R21.5) — 100% branch coverage required."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from lifeos.domain.errors import DomainError
from lifeos.domain.timeutil import (
    block_instants,
    iso_week_bounds,
    iso_week_start,
    local_date,
    local_day_bounds,
    local_weekday_sunday0,
    resolve_local,
    to_local,
    zone,
)

NY = "America/New_York"
LORD_HOWE = "Australia/Lord_Howe"  # 30-minute DST shift


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --------------------------------------------------------------------------- zone validation


def test_zone_valid_and_invalid() -> None:
    assert zone("Africa/Nairobi").key == "Africa/Nairobi"
    with pytest.raises(DomainError) as exc:
        zone("Mars/Olympus_Mons")
    assert exc.value.code == "VALIDATION_ERROR"
    with pytest.raises(DomainError):
        zone("../../etc/passwd")


# --------------------------------------------------------------------------- resolve_local


def test_ordinary_time() -> None:
    assert resolve_local(date(2026, 7, 1), time(9, 0), NY) == utc(2026, 7, 1, 13, 0)  # EDT = UTC-4
    assert resolve_local(date(2026, 1, 15), time(9, 0), NY) == utc(2026, 1, 15, 14, 0)  # EST = UTC-5
    assert resolve_local(date(2026, 1, 15), time(9, 0), zone("Africa/Nairobi")) == utc(2026, 1, 15, 6, 0)


def test_spring_forward_gap_advances_to_next_valid_minute() -> None:
    # 2026-03-08 02:00–02:59 does not exist in New York; 02:30 → 03:00 EDT (= 07:00 UTC).
    assert resolve_local(date(2026, 3, 8), time(2, 30), NY) == utc(2026, 3, 8, 7, 0)
    assert resolve_local(date(2026, 3, 8), time(2, 0), NY) == utc(2026, 3, 8, 7, 0)
    assert resolve_local(date(2026, 3, 8), time(1, 59), NY) == utc(2026, 3, 8, 6, 59)  # still EST


def test_fall_back_ambiguous_uses_first_occurrence() -> None:
    # 2026-11-01 01:30 happens twice in New York; the first is EDT (UTC-4) → 05:30 UTC.
    assert resolve_local(date(2026, 11, 1), time(1, 30), NY) == utc(2026, 11, 1, 5, 30)


def test_lord_howe_half_hour_gap() -> None:
    # Lord Howe springs forward 02:00 → 02:30 on 2026-10-04 (UTC+10:30 → UTC+11).
    assert resolve_local(date(2026, 10, 4), time(2, 15), LORD_HOWE) == utc(2026, 10, 3, 15, 30)
    assert resolve_local(date(2026, 10, 4), time(2, 30), LORD_HOWE) == utc(2026, 10, 3, 15, 30)


def test_input_tzinfo_and_fold_ignored() -> None:
    aware = time(1, 30, tzinfo=UTC, fold=1)
    assert resolve_local(date(2026, 11, 1), aware, NY) == utc(2026, 11, 1, 5, 30)


# --------------------------------------------------------------------------- local dates and days


def test_local_date_crosses_midnight() -> None:
    instant = utc(2026, 9, 21, 22, 30)
    assert local_date(instant, "UTC") == date(2026, 9, 21)
    assert local_date(instant, "Africa/Nairobi") == date(2026, 9, 22)  # UTC+3
    assert local_date(instant, zone(NY)) == date(2026, 9, 21)


def test_to_local_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_local(datetime(2026, 1, 1), NY)  # noqa: DTZ001


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25), (date(2026, 7, 1), 24)],
)
def test_local_day_bounds_dst_lengths(day: date, hours: int) -> None:
    start, end = local_day_bounds(day, NY)
    assert end - start == timedelta(hours=hours)
    assert local_date(start, NY) == day
    assert local_date(end - timedelta(microseconds=1), NY) == day


def test_day_bounds_when_midnight_is_skipped() -> None:
    # Chile (America/Santiago) springs forward at 00:00 on 2026-09-06: local midnight does not exist.
    start, end = local_day_bounds(date(2026, 9, 6), "America/Santiago")
    assert to_local(start, "America/Santiago").time() == time(1, 0)
    assert end - start == timedelta(hours=23)


# --------------------------------------------------------------------------- weeks


def test_iso_week_start_and_bounds() -> None:
    assert iso_week_start(date(2026, 9, 21)) == date(2026, 9, 21)  # Monday
    assert iso_week_start(date(2026, 9, 27)) == date(2026, 9, 21)  # Sunday
    start, end = iso_week_bounds(date(2026, 9, 24), "UTC")
    assert (start, end) == (utc(2026, 9, 21), utc(2026, 9, 28))
    ny_start, ny_end = iso_week_bounds(date(2026, 10, 28), NY)  # Mon Oct 26 – Sun Nov 1 contains fall-back
    assert ny_end - ny_start == timedelta(days=7, hours=1)


def test_weekday_sunday0() -> None:
    assert local_weekday_sunday0(date(2026, 9, 20)) == 0  # Sunday
    assert local_weekday_sunday0(date(2026, 9, 21)) == 1  # Monday
    assert local_weekday_sunday0(date(2026, 9, 26)) == 6  # Saturday


# --------------------------------------------------------------------------- time blocks


def test_block_same_day() -> None:
    assert block_instants(date(2026, 9, 21), time(9), time(10), "UTC") == (
        utc(2026, 9, 21, 9),
        utc(2026, 9, 21, 10),
    )


def test_block_crossing_midnight() -> None:
    start, end = block_instants(date(2026, 9, 21), time(23, 30), time(0, 30), "UTC")
    assert (start, end) == (utc(2026, 9, 21, 23, 30), utc(2026, 9, 22, 0, 30))


def test_block_zero_duration_rejected() -> None:
    with pytest.raises(DomainError):
        block_instants(date(2026, 9, 21), time(9), time(9), "UTC")


def test_block_entirely_inside_dst_gap_gets_minimal_duration() -> None:
    start, end = block_instants(date(2026, 3, 8), time(2, 0), time(2, 30), NY)
    assert start == utc(2026, 3, 8, 7, 0)
    assert end == start + timedelta(minutes=1)


def test_block_spanning_fall_back_is_real_duration() -> None:
    start, end = block_instants(date(2026, 11, 1), time(0, 30), time(2, 30), NY)
    assert end - start == timedelta(hours=3)  # the 01:xx hour happens twice
