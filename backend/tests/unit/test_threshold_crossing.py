"""T6.3: the integrity threshold trigger fires once per downward crossing (design §11.5; R9.14)."""

from datetime import date
from decimal import Decimal

import pytest

from lifeos.domain.integrity import crossed_below, crossing_dedupe_key

THRESHOLD = 70


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (Decimal("80.0"), Decimal("69.9"), True),  # crossing down
        (None, Decimal("50.0"), True),  # first score, already below (§11.5: null counts as above)
        (Decimal("70.0"), Decimal("69.9"), True),  # the threshold itself is "not below"
        (Decimal("60.0"), Decimal("50.0"), False),  # already below: no new crossing
        (Decimal("60.0"), Decimal("70.0"), False),  # recovery
        (Decimal("80.0"), Decimal("75.0"), False),
        (Decimal("80.0"), None, False),  # denominator went empty
        (None, None, False),
    ],
)
def test_crossed_below(previous: Decimal | None, current: Decimal | None, expected: bool) -> None:
    assert crossed_below(previous, current, THRESHOLD) is expected


def test_dedupe_key_is_per_crossing_date() -> None:
    assert crossing_dedupe_key(date(2026, 9, 21)) == "integrity_below:2026-09-21"
    assert crossing_dedupe_key(date(2026, 9, 21)) != crossing_dedupe_key(date(2026, 9, 22))
