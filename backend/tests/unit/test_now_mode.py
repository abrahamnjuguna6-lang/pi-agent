"""T8.2: Now Mode candidate ordering (design §16.7; R7.2–7.4)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from lifeos.domain.now_mode import ActionView, behind_schedule, current_block, next_unstarted, plan

NOW = datetime(2026, 9, 21, 10, 30, tzinfo=UTC)


def action(
    title: str,
    start_hour: float,
    duration_minutes: int = 60,
    status: str = "Planned",
    priority: int = 0,
) -> ActionView:
    start = datetime(2026, 9, 21, tzinfo=UTC) + timedelta(hours=start_hour)
    return ActionView(
        id=uuid.uuid5(uuid.NAMESPACE_OID, title),
        title=title,
        start=start,
        end=start + timedelta(minutes=duration_minutes),
        status=status,
        goal_priority=priority,
    )


def titles(candidates: Sequence[Any]) -> list[str]:
    return [candidate.title for candidate in candidates]


def test_the_current_block_is_recommended_first() -> None:  # R7.2
    now_block = action("Deep work", 10)  # 10:00–11:00, now 10:30
    later = action("Gym", 18)
    result = plan([later, now_block], NOW)
    assert result.recommended is not None
    assert (result.recommended.kind, result.recommended.title) == ("current_block", "Deep work")
    assert titles(result.candidates) == ["Deep work", "Gym"]  # the next unstarted follows


def test_started_actions_come_before_planned_ones_in_the_same_block() -> None:
    started = action("Writing", 10, status="Started")
    planned = action("Email", 10, priority=5)
    assert titles(current_block([planned, started], NOW)) == ["Writing", "Email"]


def test_within_the_block_earlier_start_then_goal_priority_wins() -> None:
    early = action("Standup", 9.75, duration_minutes=120)  # 09:45–11:45
    low = action("Admin", 10, priority=1)
    high = action("Strategy", 10, priority=4)
    assert titles(current_block([low, high, early], NOW)) == ["Standup", "Strategy", "Admin"]


def test_behind_schedule_is_always_reported() -> None:  # R7.4
    late_planned = action("Morning run", 7)
    late_started = action("Report", 8, status="Started")
    done = action("Breakfast", 8, status="Completed")
    result = plan([late_planned, late_started, done], NOW)
    assert titles(result.behind_schedule) == ["Morning run", "Report"]
    assert titles(behind_schedule([done], NOW)) == []


def test_next_unstarted_is_the_next_planned_action_today() -> None:  # R7.3
    actions = [action("Gym", 18), action("Lunch", 12), action("Done later", 13, status="Completed")]
    upcoming = next_unstarted(actions, NOW)
    assert upcoming is not None
    assert upcoming.title == "Lunch"


def test_when_the_current_block_is_complete_the_next_action_leads() -> None:  # R7.3
    finished = action("Deep work", 10, status="Completed")
    upcoming = action("Lunch", 12)
    result = plan([finished, upcoming], NOW)
    assert result.recommended is not None
    assert (result.recommended.kind, result.recommended.title) == ("next_unstarted", "Lunch")


def test_buffer_task_when_nothing_is_left_today() -> None:  # R7.3
    finished = action("Deep work", 10, status="Completed")
    task_id = uuid.uuid4()
    result = plan([finished], NOW, buffer_task=(task_id, "Draft the proposal"))
    assert result.recommended is not None
    assert (result.recommended.kind, result.recommended.id) == ("buffer_task", task_id)
    assert "highest-priority goal" in result.recommended.reason


def test_buffer_goal_when_there_is_no_open_task() -> None:
    goal_id = uuid.uuid4()
    result = plan([], NOW, buffer_task=None, buffer_goal=(goal_id, "Run a marathon"))
    assert result.recommended is not None
    assert (result.recommended.kind, result.recommended.id) == ("buffer_goal", goal_id)


def test_an_empty_day_with_no_goals_recommends_nothing() -> None:
    result = plan([], NOW)
    assert (result.candidates, result.behind_schedule, result.recommended) == ([], [], None)


def test_a_behind_schedule_action_does_not_suppress_the_buffer() -> None:
    """Being late is reported, but it is not a recommendation of what to do now."""
    late = action("Morning run", 7)
    goal_id = uuid.uuid4()
    result = plan([late], NOW, buffer_goal=(goal_id, "Fitness"))
    assert titles(result.behind_schedule) == ["Morning run"]
    assert result.recommended is not None
    assert result.recommended.kind == "buffer_goal"


def test_json_carries_the_ids_the_grounding_validator_checks() -> None:  # design §16.7
    result = plan([action("Deep work", 10)], NOW)
    data = result.as_json()
    assert data["recommended_id"] == data["now_candidates"][0]["id"]
    assert set(data) == {"now_candidates", "behind_schedule", "recommended_id"}
