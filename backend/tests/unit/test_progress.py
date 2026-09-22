"""T4.3: objective/goal progress (design §16.1, §38.1 "Progress") — 100% branch coverage."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lifeos.domain.errors import DomainError
from lifeos.domain.progress import (
    WeightedProgress,
    goal_progress,
    objective_progress,
    validate_range,
)

D = Decimal


# --------------------------------------------------------------------------- design §38.1 table


def test_higher_is_better_partial() -> None:
    assert objective_progress("higher_is_better", D(3), D(12), None) == D("25.00")


def test_higher_is_better_over_target_clamped() -> None:
    assert objective_progress("higher_is_better", D(15), D(12), None) == D("100.00")


def test_higher_is_better_negative_clamped_to_zero() -> None:
    assert objective_progress("higher_is_better", D(-5), D(12), None) == D("0.00")


def test_lower_is_better_requirements_example() -> None:
    # R1.5 example: baseline 100, target 20, current 60 → 50%.
    assert objective_progress("lower_is_better", D(60), D(20), D(100)) == D("50.00")


def test_lower_is_better_worse_than_baseline_clamped() -> None:
    assert objective_progress("lower_is_better", D(120), D(20), D(100)) == D("0.00")


def test_lower_is_better_beyond_target_clamped() -> None:
    assert objective_progress("lower_is_better", D(10), D(20), D(100)) == D("100.00")


def test_rounding_half_up_to_two_places() -> None:
    assert objective_progress("higher_is_better", D(1), D(3), None) == D("33.33")
    assert objective_progress("higher_is_better", D(2), D(3), None) == D("66.67")


@pytest.mark.parametrize(
    ("direction", "target", "baseline", "field"),
    [
        ("higher_is_better", D(0), None, "target_value"),
        ("lower_is_better", D(20), None, "baseline_value"),
        ("lower_is_better", D(20), D(20), "baseline_value"),
    ],
)
def test_invalid_ranges_rejected(
    direction: str, target: Decimal, baseline: Decimal | None, field: str
) -> None:
    with pytest.raises(DomainError) as exc:
        validate_range(direction, target, baseline)  # type: ignore[arg-type]
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.details["field"] == field


def test_goal_weighted_average() -> None:
    # weights 1 and 3: (100×1 + 20×3) / 4 = 40
    objectives = [WeightedProgress(D(100), D(1)), WeightedProgress(D(20), D(3))]
    assert goal_progress(objectives) == D("40.00")


def test_goal_without_active_objectives_is_zero() -> None:
    assert goal_progress([]) == D("0.00")  # R1.7


def test_goal_with_non_positive_total_weight_is_zero() -> None:
    assert goal_progress([WeightedProgress(D(50), D(0))]) == D("0.00")


# --------------------------------------------------------------------------- properties

decimals = st.decimals(min_value=-10_000, max_value=10_000, allow_nan=False, allow_infinity=False, places=3)


@given(current=decimals, target=decimals.filter(lambda d: d != 0))
def test_higher_is_better_always_within_bounds(current: Decimal, target: Decimal) -> None:
    assert D(0) <= objective_progress("higher_is_better", current, target, None) <= D(100)


@given(current=decimals, target=decimals, baseline=decimals)
def test_lower_is_better_always_within_bounds(current: Decimal, target: Decimal, baseline: Decimal) -> None:
    if baseline == target:
        return
    assert D(0) <= objective_progress("lower_is_better", current, target, baseline) <= D(100)


@given(
    st.lists(
        st.tuples(
            st.decimals(min_value=0, max_value=100, places=2),
            st.decimals(min_value="0.01", max_value=1000, places=2),
        ),
        max_size=20,
    )
)
def test_goal_progress_always_within_bounds(pairs: list[tuple[Decimal, Decimal]]) -> None:
    value = goal_progress([WeightedProgress(p, w) for p, w in pairs])
    assert D(0) <= value <= D(100)
    if pairs:
        assert min(p for p, _ in pairs) - D("0.01") <= value <= max(p for p, _ in pairs) + D("0.01")
