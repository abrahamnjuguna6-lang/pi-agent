"""T6.2: deadline evaluation at local-midnight boundaries in three timezones (design §11.3)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from lifeos.domain.commitments import EXPLANATION_WINDOW, deadline_action
from lifeos.domain.timeutil import local_date, local_day_bounds

DUE = date(2026, 9, 21)
# UTC+14, UTC+0 and UTC-10: the same instant is a different local date in each
ZONES = ["Pacific/Kiritimati", "UTC", "Pacific/Honolulu"]


def evaluate(status: str, now: datetime, tz: str, window: datetime | None = None) -> str | None:
    return deadline_action(status, DUE, local_date(now, tz), window, now)


@pytest.mark.parametrize(
    ("tz", "last_moment_utc"),
    [
        ("Pacific/Kiritimati", datetime(2026, 9, 21, 9, 59, tzinfo=UTC)),  # 23:59 local on the 21st
        ("UTC", datetime(2026, 9, 21, 23, 59, tzinfo=UTC)),
        ("Pacific/Honolulu", datetime(2026, 9, 22, 9, 59, tzinfo=UTC)),
    ],
)
def test_open_breaks_only_after_the_local_day_ends(tz: str, last_moment_utc: datetime) -> None:
    assert evaluate("Open", last_moment_utc, tz) is None  # R9.7: still the due local day
    assert evaluate("Open", last_moment_utc + timedelta(minutes=1), tz) == "break"


@pytest.mark.parametrize("tz", ZONES)
def test_the_same_instant_differs_by_timezone(tz: str) -> None:
    # 19:00 on the 22nd in Kiritimati, but still 19:00 on the 21st in Honolulu
    instant = datetime(2026, 9, 22, 5, 0, tzinfo=UTC)
    expected = {"Pacific/Kiritimati": "break", "UTC": "break", "Pacific/Honolulu": None}[tz]
    assert evaluate("Open", instant, tz) == expected


@pytest.mark.parametrize("tz", ZONES)
def test_deferred_opens_a_window_then_breaks(tz: str) -> None:
    _, due_end = local_day_bounds(DUE, tz)
    just_after = due_end + timedelta(minutes=1)
    assert evaluate("Deferred", due_end - timedelta(minutes=1), tz) is None  # not overdue yet
    assert evaluate("Deferred", just_after, tz) == "open_window"

    window_ends = due_end + EXPLANATION_WINDOW  # the window starts when the due date passes
    assert evaluate("Deferred", window_ends - timedelta(seconds=1), tz, window_ends) is None
    assert evaluate("Deferred", window_ends, tz, window_ends) == "break"  # R9.10


def test_dst_day_still_ends_at_local_midnight() -> None:
    tz = "Europe/Berlin"  # clocks go back on 2026-10-25, so that local day is 25 h long
    due_end = local_day_bounds(date(2026, 10, 25), tz)[1]
    assert due_end == datetime(2026, 10, 24, 22, 0, tzinfo=UTC) + timedelta(hours=25)
    assert deadline_action("Open", date(2026, 10, 25), local_date(due_end, tz), None, due_end) == "break"
