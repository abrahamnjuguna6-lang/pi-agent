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


def routine_template(t: m.RoutineTemplate, entries: list[m.RoutineEntry] | None = None) -> dict[str, Any]:
    data = {k: _v(getattr(t, k)) for k in ("id", "title", "created_at", "updated_at")}
    data["active_days"] = list(t.active_days)
    if entries is not None:
        data["entries"] = [routine_entry(e) for e in entries]
    return data


def routine_entry(e: m.RoutineEntry) -> dict[str, Any]:
    data = {
        k: _v(getattr(e, k))
        for k in (
            "id",
            "routine_template_id",
            "title",
            "start_time",
            "end_time",
            "sort_order",
            "goal_id",
            "habit_id",
            "created_at",
            "updated_at",
        )
    }
    data["crosses_midnight"] = e.end_time < e.start_time
    return data


def habit(h: m.Habit, paused: bool | None = None) -> dict[str, Any]:
    data = {
        k: _v(getattr(h, k))
        for k in (
            "id",
            "goal_id",
            "project_id",
            "title",
            "recurrence_type",
            "preferred_start",
            "duration_minutes",
            "frequency_target",
            "start_date",
            "end_date",
            "status",
            "created_at",
            "updated_at",
        )
    }
    data["recurrence_days"] = list(h.recurrence_days) if h.recurrence_days else None
    if paused is not None:
        data["paused"] = paused
    return data


def daily_action(a: m.DailyAction) -> dict[str, Any]:
    return {
        k: _v(getattr(a, k))
        for k in (
            "id",
            "title",
            "date",
            "occurrence_date",
            "scheduled_start",
            "scheduled_end",
            "status",
            "lifecycle_state",
            "cancelled_at",
            "source_type",
            "source_id",
            "completed_at",
            "created_at",
            "updated_at",
        )
    }


def checkin(c: m.CheckinRecord) -> dict[str, Any]:
    return {
        k: _v(getattr(c, k))
        for k in (
            "id",
            "daily_action_id",
            "previous_status",
            "new_status",
            "transition_source",
            "note",
            "created_at",
        )
    }


def schedule_change(h: m.DailyActionScheduleHistory) -> dict[str, Any]:
    return {
        k: _v(getattr(h, k))
        for k in (
            "id",
            "daily_action_id",
            "change_type",
            "previous_start",
            "previous_end",
            "new_start",
            "new_end",
            "changed_by",
            "reason",
            "changed_at",
        )
    }


def habit_occurrence(r: m.HabitOccurrenceRecord) -> dict[str, Any]:
    return {
        k: _v(getattr(r, k))
        for k in (
            "id",
            "habit_id",
            "occurrence_date",
            "daily_action_id",
            "result",
            "completion_percent",
            "note",
            "created_at",
        )
    }


def habit_metrics(metrics: Any, pauses: list[m.HabitPausePeriod]) -> dict[str, Any]:
    return {
        "period_kind": metrics.period_kind,
        "current_streak": metrics.current_streak,
        "longest_streak": metrics.longest_streak,
        "missed": metrics.missed,
        "partials": metrics.partials,
        "total_completions": metrics.total_completions,
        "periods": [
            {
                "start": p.start.isoformat(),
                "end": p.end.isoformat(),
                "completed": p.completed,
                "required": p.required,
                "met": p.met,
                "excluded": p.excluded,
                "in_progress": p.in_progress,
            }
            for p in metrics.periods
        ],
        "pause_periods": [
            {
                "starts_on": p.starts_on.isoformat(),
                "ends_on": p.ends_on.isoformat() if p.ends_on else None,
                "reason": p.reason,
            }
            for p in pauses
        ],
    }


def schedule_suggestion(x: m.ScheduleSuggestion) -> dict[str, Any]:
    data = {
        k: _v(getattr(x, k))
        for k in ("id", "trigger_action_id", "local_date", "status", "decided_at", "created_at")
    }
    data["proposal"] = x.proposal
    return data


def commitment(c: m.Commitment) -> dict[str, Any]:
    return {
        k: _v(getattr(c, k))
        for k in (
            "id",
            "title",
            "source",
            "due_date",
            "current_due_date",
            "completion_condition",
            "status",
            "deferral_count",
            "explanation_window_ends_at",
            "goal_category",
            "kept_at",
            "broken_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        )
    }


def commitment_link(link: m.CommitmentLink) -> dict[str, Any]:
    return {
        k: _v(getattr(link, k)) for k in ("id", "entity_type", "entity_id", "removed_at", "removed_reason")
    }


def commitment_event(e: m.CommitmentEvent) -> dict[str, Any]:
    return {
        k: _v(getattr(e, k))
        for k in (
            "id",
            "event_type",
            "previous_status",
            "new_status",
            "previous_due",
            "new_due",
            "explanation",
            "actor",
            "created_at",
        )
    }


def commitment_detail(detail: Any) -> dict[str, Any]:
    return commitment(detail.commitment) | {
        "links": [commitment_link(link) for link in detail.links],
        "events": [commitment_event(e) for e in detail.events],
    }


def integrity_snapshot(snapshot: m.IntegrityScoreSnapshot) -> dict[str, Any]:
    return {
        k: _v(getattr(snapshot, k))
        for k in ("local_date", "score", "kept", "broken", "overdue_deferred", "computed_at")
    }


def integrity_overview(overview: dict[str, Any]) -> dict[str, Any]:
    return integrity_snapshot(overview["current"]) | {
        "threshold": overview["threshold"],
        "below_threshold": _below(overview["current"].score, overview["threshold"]),
        "trend": {
            "baseline_date": _v(overview["baseline_date"]),
            "baseline_score": _v(overview["baseline_score"]),
            "delta": _v(overview["delta"]),
        },
        "series": [integrity_snapshot(row) for row in overview["series"]],
    }


def _below(score: Any, threshold: int) -> bool:
    return score is not None and score < threshold


def escalation(view: Any) -> dict[str, Any]:
    state = view.state
    return {
        k: _v(getattr(state, k))
        for k in (
            "id",
            "source_type",
            "source_id",
            "level",
            "escalation_episode_id",
            "episode_started_at",
            "reflection_required",
            "reflection_completed_at",
            "reflection_id",
            "recovery_window_anchor_date",
            "last_reduced_at",
            "last_evaluated_at",
            "created_at",
            "updated_at",
        )
    } | {
        "source_title": view.source_title,
        "goal_id": _v(view.goal_id),
        "skips": [{"date": day.isoformat(), "reason": reason} for day, reason in view.skips],
    }


def reflection(r: m.Reflection) -> dict[str, Any]:
    return {
        k: _v(getattr(r, k)) for k in ("id", "date", "type", "content", "escalation_episode_id", "created_at")
    } | {"answers": r.answers, "goal_categories": list(r.goal_categories)}


def memory_entry(e: m.MemoryStoreEntry) -> dict[str, Any]:
    data = {
        k: _v(getattr(e, k))
        for k in (
            "id",
            "content",
            "type",
            "source",
            "importance",
            "confidence",
            "is_inference",
            "source_ref_type",
            "source_ref_id",
            "superseded_by",
            "embedding_status",
            "created_at",
            "updated_at",
        )
    }
    data["categories"] = list(e.categories)
    return data


def memory_hit(hit: Any) -> dict[str, Any]:
    return memory_entry(hit.entry) | {"similarity": round(hit.similarity, 6)}
