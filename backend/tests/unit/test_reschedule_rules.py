"""T5.6: source-aware reschedule rules and NL matching (design §12.2–12.4, §38.1 scheduling table)."""

from datetime import time

import pytest

from lifeos.domain.errors import DomainError
from lifeos.domain.schedule.reschedule import (
    ensure_cancellable,
    ensure_reschedulable,
    reschedule_effect,
    score_candidate,
    title_similarity,
)


@pytest.mark.parametrize(
    ("source", "effect"),
    [
        ("ROUTINE_ENTRY", "routine_exception"),  # Template unchanged
        ("HABIT", "habit_override"),  # recurrence unchanged
        ("TASK", "task_update"),  # task.scheduled_date/time updated
        ("MANUAL", "direct"),
    ],
)
def test_effect_per_source(source: str, effect: str) -> None:
    assert reschedule_effect(source) == effect


def test_only_planned_active_actions_reschedule() -> None:
    ensure_reschedulable("Planned", "active")
    for status in ("Started", "Completed", "Skipped"):
        with pytest.raises(DomainError) as exc:
            ensure_reschedulable(status, "active")
        assert exc.value.code == "INVALID_TRANSITION"
    with pytest.raises(DomainError):
        ensure_reschedulable("Planned", "cancelled")


def test_cancellable_states() -> None:
    ensure_cancellable("Planned", "active")
    ensure_cancellable("Started", "active")
    for status in ("Completed", "Skipped"):
        with pytest.raises(DomainError):
            ensure_cancellable(status, "active")
    with pytest.raises(DomainError):
        ensure_cancellable("Planned", "cancelled")


def test_title_similarity() -> None:
    assert title_similarity("Learning session", "learning") == 1.0  # containment
    assert title_similarity("Evening run", "evening rn") > 0.8  # typo tolerance
    assert title_similarity("Deep work", "Groceries") < 0.6
    assert title_similarity("", "x") == 0.0


def test_score_by_time_window() -> None:
    assert score_candidate(time(16, 0), "Learning", time(16, 10), None) == (10.0, None)
    assert score_candidate(time(16, 0), "Learning", time(16, 15), None) == (15.0, None)
    assert score_candidate(time(16, 0), "Learning", time(16, 16), None) is None


def test_score_requires_every_given_criterion() -> None:
    assert score_candidate(time(16, 0), "Learning session", time(16, 0), "learning") == (0.0, 1.0)
    assert score_candidate(time(16, 0), "Groceries", time(16, 0), "learning") is None
    minutes, title = score_candidate(time(9, 0), "Learning", None, "learning") or (None, None)
    assert (minutes, title) == (None, 1.0)
