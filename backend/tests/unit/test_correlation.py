"""T8.1: correlation gating and degenerate input (design §16.5, §38.1; R11.6–11.9)."""

from __future__ import annotations

import random

import pytest

from lifeos.domain.analytics.correlation import ALPHA, MIN_SIGNIFICANT, correlate, is_normal


def linear(n: int) -> tuple[list[float], list[float]]:
    """A perfectly monotonic pair: significant as soon as the sample is large enough."""
    xs = [float(index) for index in range(n)]
    return xs, [value * 2 + 1 for value in xs]


def noisy(n: int, seed: int = 7) -> tuple[list[float], list[float]]:
    """Unrelated series: a coefficient near zero, so p stays well above 0.05."""
    rng = random.Random(seed)  # deterministic test data, not a secret
    xs = [rng.uniform(1, 5) for _ in range(n)]
    ys = [rng.uniform(0, 1) for _ in range(n)]
    return xs, ys


@pytest.mark.parametrize("n", [0, 1, 6])
def test_below_seven_observations_nothing_is_computed(n: int) -> None:  # R11.6
    result = correlate(*linear(n))
    assert (result.status, result.n, result.coefficient, result.p_value) == ("none", n, None, None)
    assert result.surfaceable is False


@pytest.mark.parametrize("n", [7, 19])
def test_seven_to_nineteen_is_preliminary_only(n: int) -> None:  # R11.6
    result = correlate(*linear(n))
    assert (result.status, result.n, result.method) == ("preliminary", n, "spearman")
    assert result.coefficient == pytest.approx(1.0)
    assert result.p_value is None  # no significance claim below 20 observations
    assert result.surfaceable is False


def test_twenty_observations_with_a_strong_relationship_is_significant() -> None:  # R11.7–11.8
    result = correlate(*linear(MIN_SIGNIFICANT))
    assert (result.status, result.n, result.method) == ("significant", 20, "spearman")
    assert result.coefficient == pytest.approx(1.0)
    assert result.p_value is not None
    assert result.p_value < ALPHA
    assert result.surfaceable is True


def test_twenty_observations_without_a_relationship_is_not_significant() -> None:
    result = correlate(*noisy(20))
    assert result.status == "not_significant"
    assert result.p_value is not None
    assert result.p_value >= ALPHA
    assert result.surfaceable is False  # R11.8: no finding is surfaced


def test_ties_are_handled() -> None:
    xs = [1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0, 5.0, 5.0] * 2
    ys = [0.1, 0.2, 0.3, 0.35, 0.5, 0.55, 0.7, 0.75, 0.9, 0.95] * 2
    result = correlate(xs, ys)
    assert result.status == "significant"
    assert result.coefficient is not None
    assert result.coefficient > 0.9


def test_a_constant_variable_has_no_coefficient() -> None:
    """scipy returns NaN for a constant input; that is reported as not significant, not a crash."""
    xs = [3.0] * 25
    _, ys = linear(25)
    result = correlate(xs, ys)
    assert (result.status, result.method) == ("not_significant", "spearman")
    assert (result.coefficient, result.p_value) == (None, None)
    assert result.surfaceable is False


def test_both_variables_constant() -> None:
    result = correlate([2.0] * 21, [0.5] * 21)
    assert (result.status, result.coefficient) == ("not_significant", None)


def test_pearson_is_reported_only_when_both_variables_are_normal() -> None:  # R11.7
    rng = random.Random(1)  # deterministic test data; both series pass Shapiro–Wilk
    xs = [rng.gauss(3, 1) for _ in range(40)]
    ys = [value * 0.5 + rng.gauss(0, 0.1) for value in xs]
    normal = correlate(xs, ys)
    assert normal.method == "spearman"  # Spearman stays the headline method
    assert normal.pearson is not None
    assert normal.pearson.method == "pearson"
    assert normal.pearson.coefficient is not None
    assert normal.pearson.coefficient > 0.9

    skewed_x = [float(value**3) for value in range(1, 41)]  # far from normal
    skewed = correlate(skewed_x, [float(value) for value in range(1, 41)])
    assert skewed.pearson is None


def test_is_normal_rejects_constant_and_tiny_samples() -> None:
    assert is_normal([1.0, 1.0, 1.0, 1.0, 1.0]) is False
    assert is_normal([1.0, 2.0]) is False


def test_mismatched_series_are_a_programming_error() -> None:
    with pytest.raises(ValueError, match="paired"):
        correlate([1.0, 2.0], [1.0])


def test_json_shape_carries_everything_a_finding_must_report() -> None:  # R11.8
    result = correlate(*linear(25))
    data = result.as_json()
    assert set(data) >= {"method", "n", "coefficient", "p_value", "status"}
    assert (data["n"], data["status"], data["method"]) == (25, "significant", "spearman")
