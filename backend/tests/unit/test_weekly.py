"""T8.4: weekly ranking rules and category attribution (design §16.10; R10.2)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from lifeos.domain.analytics.weekly import (
    UNLINKED,
    Gap,
    Win,
    category_rates,
    previous_week,
    rank_gaps,
    rank_wins,
    week_bounds,
)


def win(title: str, priority: int = 0, completions: int = 0, delta: str = "0") -> Win:
    return Win("source", uuid.uuid5(uuid.NAMESPACE_OID, title), title, completions, priority, Decimal(delta))


def gap(title: str, misses: int = 0, priority: int = 0) -> Gap:
    return Gap("source", uuid.uuid5(uuid.NAMESPACE_OID, title), title, misses, priority)


def titles(items: list[Win] | list[Gap]) -> list[str]:
    return [item.title for item in items]


def test_wins_rank_by_goal_priority_then_completions_then_delta() -> None:
    items = [
        win("low priority, many completions", priority=1, completions=6),
        win("high priority, few completions", priority=5, completions=2),
        win("high priority, more completions", priority=5, completions=4),
    ]
    assert titles(rank_wins(items)) == [
        "high priority, more completions",
        "high priority, few completions",
        "low priority, many completions",
    ]


def test_progress_delta_breaks_a_win_tie() -> None:
    items = [win("smaller move", 3, 2, "1.5"), win("bigger move", 3, 2, "12.0")]
    assert titles(rank_wins(items)) == ["bigger move", "smaller move"]


def test_only_the_top_three_wins_are_kept() -> None:  # R10.2
    items = [win(f"win {index}", priority=index) for index in range(6)]
    assert titles(rank_wins(items)) == ["win 5", "win 4", "win 3"]


def test_gaps_rank_by_miss_count_then_priority() -> None:
    items = [
        gap("high priority, one miss", misses=1, priority=5),
        gap("low priority, many misses", misses=4, priority=1),
        gap("same misses, higher priority", misses=4, priority=3),
    ]
    assert titles(rank_gaps(items)) == [
        "same misses, higher priority",
        "low priority, many misses",
        "high priority, one miss",
    ]


def test_ranking_is_stable_for_identical_scores() -> None:
    """Two candidates that tie on every rule are ordered by title, so runs do not shuffle."""
    items = [win("zebra", 2, 2), win("alpha", 2, 2)]
    assert titles(rank_wins(items)) == ["alpha", "zebra"]
    assert titles(rank_wins(list(reversed(items)))) == ["alpha", "zebra"]


def test_empty_input_ranks_to_nothing() -> None:
    assert rank_wins([]) == []
    assert rank_gaps([]) == []


def test_category_rates_sort_by_rate_with_unlinked_last() -> None:  # design §16.10
    rows = category_rates({"Career": (1, 4), "Fitness": (3, 4), UNLINKED: (4, 4)})
    assert [row.category for row in rows] == ["Fitness", "Career", UNLINKED]
    assert [str(row.rate) for row in rows] == ["0.7500", "0.2500", "1.0000"]


def test_a_category_with_nothing_scheduled_has_no_rate() -> None:
    (row,) = category_rates({"Career": (0, 0)})
    assert (row.rate, row.completed, row.total) == (None, 0, 0)


def test_week_bounds_are_monday_to_sunday() -> None:
    assert week_bounds(date(2026, 9, 23)) == (date(2026, 9, 21), date(2026, 9, 27))  # a Wednesday
    assert week_bounds(date(2026, 9, 21)) == (date(2026, 9, 21), date(2026, 9, 27))  # the Monday
    assert week_bounds(date(2026, 9, 27)) == (date(2026, 9, 21), date(2026, 9, 27))  # the Sunday


def test_previous_week_is_the_seven_days_before_this_one() -> None:
    assert previous_week(date(2026, 9, 23)) == (date(2026, 9, 14), date(2026, 9, 20))


def test_win_and_gap_json_shapes() -> None:
    assert win("Run", 3, 2, "5.5").as_json() == {
        "kind": "source",
        "id": str(uuid.uuid5(uuid.NAMESPACE_OID, "Run")),
        "title": "Run",
        "completions": 2,
        "goal_priority": 3,
        "progress_delta": "5.5",
    }
    assert gap("Run", 2, 3).as_json()["misses"] == 2
