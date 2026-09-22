"""ORM → JSON-safe dicts for API responses. Decimals are strings (exact); dates ISO-8601."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from lifeos.db import models as m
from lifeos.domain.profile import Profile


def _v(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if value is not None and hasattr(value, "hex") and hasattr(value, "version"):  # UUID
        return str(value)
    return value


def goal(g: m.Goal) -> dict[str, Any]:
    return {
        k: _v(getattr(g, k))
        for k in (
            "id",
            "title",
            "category",
            "description",
            "target_date",
            "status",
            "progress",
            "priority",
            "archived_at",
            "created_at",
            "updated_at",
        )
    }


def objective(o: m.Objective) -> dict[str, Any]:
    return {
        k: _v(getattr(o, k))
        for k in (
            "id",
            "goal_id",
            "title",
            "metric_direction",
            "current_value",
            "target_value",
            "baseline_value",
            "unit",
            "weight",
            "target_date",
            "status",
            "progress",
            "created_at",
            "updated_at",
        )
    }


def project(p: m.Project) -> dict[str, Any]:
    return {
        k: _v(getattr(p, k))
        for k in ("id", "objective_id", "title", "description", "status", "created_at", "updated_at")
    }


def task(t: m.Task) -> dict[str, Any]:
    return {
        k: _v(getattr(t, k))
        for k in (
            "id",
            "project_id",
            "goal_id",
            "title",
            "status",
            "due_date",
            "scheduled_date",
            "scheduled_time",
            "duration_minutes",
            "completed_at",
            "created_at",
            "updated_at",
        )
    }


def profile(p: Profile) -> dict[str, Any]:
    data = {
        k: _v(getattr(p, k))
        for k in (
            "id",
            "email",
            "email_verified",
            "full_name",
            "timezone",
            "wake_time",
            "sleep_time",
            "working_hours_start",
            "working_hours_end",
            "accountability_style",
            "integrity_score_threshold",
            "briefing_time",
            "reflection_time",
            "ceo_meeting_time",
            "values_text",
            "principles_text",
            "vision_statement",
            "onboarding_completed",
        )
    }
    data["life_categories"] = list(p.life_categories)
    data["notification_prefs"] = p.notification_prefs.model_dump(mode="json")
    return data
