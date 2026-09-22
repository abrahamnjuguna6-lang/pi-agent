"""T4.4: deterministic goal ordering (design §15.2)."""

import uuid
from datetime import UTC, date, datetime

from lifeos.db import models as m
from lifeos.domain.goals import sort_by_priority

T = datetime(2026, 1, 1, tzinfo=UTC)


def goal(
    title: str, priority: int, target: date | None = None, created: datetime = T, gid: str | None = None
) -> m.Goal:
    return m.Goal(
        id=uuid.UUID(gid) if gid else uuid.uuid4(),
        title=title,
        priority=priority,
        target_date=target,
        created_at=created,
        category="Life",
    )


def titles(goals: list[m.Goal]) -> list[str]:
    return [g.title for g in sort_by_priority(goals)]


def test_priority_descending_first() -> None:
    assert titles([goal("low", 1), goal("high", 5), goal("mid", 3)]) == ["high", "mid", "low"]


def test_dated_before_undated_at_equal_priority() -> None:
    assert titles([goal("undated", 3), goal("dated", 3, date(2027, 1, 1))]) == ["dated", "undated"]


def test_earlier_target_date_first() -> None:
    assert titles([goal("later", 3, date(2027, 6, 1)), goal("sooner", 3, date(2026, 12, 1))]) == [
        "sooner",
        "later",
    ]


def test_created_at_breaks_ties() -> None:
    older = goal("older", 3, created=T)
    newer = goal("newer", 3, created=datetime(2026, 2, 1, tzinfo=UTC))
    assert titles([newer, older]) == ["older", "newer"]


def test_id_is_final_stable_tiebreaker() -> None:
    a = goal("a", 3, gid="00000000-0000-0000-0000-000000000001")
    b = goal("b", 3, gid="00000000-0000-0000-0000-000000000002")
    assert titles([b, a]) == ["a", "b"]
    assert titles([a, b]) == ["a", "b"]


def test_no_category_weighting() -> None:
    career = goal("career", 3)
    career.category = "Career"
    family = goal("family", 3, created=datetime(2025, 1, 1, tzinfo=UTC))
    family.category = "Family"
    assert titles([career, family]) == ["family", "career"]  # decided by created_at, not category
