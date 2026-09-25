"""T6.3: integrity score arithmetic and the 30-day window (design §11.4; R9.11–9.12)."""

from datetime import date
from decimal import Decimal

import pytest

from lifeos.domain.integrity import IntegrityCounts, integrity_score, trend_delta, window_first_day


@pytest.mark.parametrize(
    ("kept", "broken", "overdue", "expected"),
    [
        (0, 0, 0, None),  # empty denominator → "No resolved commitments in the last 30 days"
        (3, 1, 0, Decimal("75.0")),
        (0, 2, 1, Decimal("0.0")),
        (5, 0, 0, Decimal("100.0")),
        (2, 1, 0, Decimal("66.7")),  # rounded half-up to 1 dp
        (1, 2, 0, Decimal("33.3")),
        (1, 1, 1, Decimal("33.3")),
        (2, 0, 1, Decimal("66.7")),  # an overdue deferred Commitment counts in the denominator (R9.11)
    ],
)
def test_score(kept: int, broken: int, overdue: int, expected: Decimal | None) -> None:
    assert integrity_score(kept, broken, overdue) == expected
    assert IntegrityCounts(kept, broken, overdue).score == expected


def test_rounding_is_half_up() -> None:
    assert integrity_score(7, 9, 0) == Decimal("43.8")  # 43.75
    assert integrity_score(1, 15, 0) == Decimal("6.3")  # 6.25


def test_window_is_30_local_days_including_today() -> None:
    assert window_first_day(date(2026, 9, 21)) == date(2026, 8, 23)
    assert (date(2026, 9, 21) - window_first_day(date(2026, 9, 21))).days == 29


@pytest.mark.parametrize(
    ("current", "baseline", "expected"),
    [
        (Decimal("80.0"), Decimal("60.0"), Decimal("20.0")),
        (Decimal("50.0"), Decimal("75.0"), Decimal("-25.0")),
        (None, Decimal("75.0"), None),
        (Decimal("75.0"), None, None),
    ],
)
def test_trend_delta(current: Decimal | None, baseline: Decimal | None, expected: Decimal | None) -> None:
    assert trend_delta(current, baseline) == expected
