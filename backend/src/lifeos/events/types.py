"""Domain event vocabulary (design §4.3)."""

from __future__ import annotations

from typing import Literal

EventType = Literal[
    "daily_action.status_changed",
    "daily_action.rescheduled",
    "daily_action.cancelled",
    "commitment.status_changed",
    "commitment.condition_changed",
    "commitment.explanation_window_opened",
    "objective.value_changed",
    "escalation.level_changed",
    "reflection.submitted",
    "user.email_requested",
    "test.event",  # used by the outbox tests only
]
