"""T1.1: clock abstraction and UTC guard."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from lifeos.domain.clock import FrozenClock, SystemClock, ensure_utc


def test_system_clock_is_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC


def test_frozen_clock_normalizes_to_utc_and_can_move(frozen_clock: FrozenClock) -> None:
    eat = timezone(timedelta(hours=3))
    frozen_clock.set(datetime(2026, 9, 21, 15, 0, tzinfo=eat))
    assert frozen_clock.now() == datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    assert frozen_clock.now().tzinfo is UTC


def test_frozen_clock_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 1, 1))  # noqa: DTZ001 - intentionally naive
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2026, 1, 1))  # noqa: DTZ001


def test_ensure_utc() -> None:
    with pytest.raises(ValueError, match="naive"):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001
    eat = timezone(timedelta(hours=3))
    assert ensure_utc(datetime(2026, 1, 1, 3, tzinfo=eat)) == datetime(2026, 1, 1, tzinfo=UTC)
