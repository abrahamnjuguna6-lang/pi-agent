"""`users.notification_prefs` schema (design §24.11, R8.9, R14.3–14.4).

Channel choice is per notification type — and therefore per accountability level. Delivery TIMES
of briefing / reflection / CEO meeting live in dedicated `users` columns, not here.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lifeos.domain.errors import DomainError

NotificationType = Literal[
    "briefing",
    "daily_reflection",
    "ceo_meeting",
    "commitment_breach",
    "commitment_explanation_due",
    "accountability_l1",
    "accountability_l2",
    "accountability_l3",
    "accountability_l4",
    "accountability_l5",
    "schedule_suggestion",
    "lesson_proposal",
    "account_security",
]

# Critical types can never be fully silenced: in-app delivery stays on (design §24.11).
CRITICAL_TYPES: frozenset[str] = frozenset({"accountability_l4", "accountability_l5", "account_security"})
_PUSH_OFF_BY_DEFAULT: frozenset[str] = frozenset({"schedule_suggestion", "lesson_proposal"})


class Channels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    push: bool = True
    in_app: bool = True


class DoNotDisturb(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    start_time: time = time(22, 0)
    end_time: time = time(7, 0)

    @model_validator(mode="after")
    def _non_empty_window(self) -> DoNotDisturb:
        if self.start_time == self.end_time:
            raise ValueError("do_not_disturb start_time and end_time must differ")
        return self


TYPE_NAMES: tuple[str, ...] = get_args(NotificationType)


def _default_types() -> dict[str, Channels]:
    return {t: Channels(push=t not in _PUSH_OFF_BY_DEFAULT, in_app=True) for t in TYPE_NAMES}


class NotificationPrefs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    do_not_disturb: DoNotDisturb = Field(default_factory=DoNotDisturb)
    types: dict[str, Channels] = Field(default_factory=_default_types)

    @model_validator(mode="after")
    def _fill_and_guard(self) -> NotificationPrefs:
        unknown = set(self.types) - set(TYPE_NAMES)
        if unknown:
            raise ValueError(f"unknown notification types: {sorted(unknown)}")
        merged = _default_types()
        merged.update(self.types)
        for t in CRITICAL_TYPES:
            if not merged[t].in_app:
                raise ValueError(f"in-app delivery cannot be disabled for {t}")
        self.types = merged
        return self

    def in_dnd(self, local: time) -> bool:
        """Whether a local time falls inside the quiet window (supports windows crossing midnight)."""
        dnd = self.do_not_disturb
        if not dnd.enabled:
            return False
        if dnd.start_time < dnd.end_time:
            return dnd.start_time <= local < dnd.end_time
        return local >= dnd.start_time or local < dnd.end_time


def parse_prefs(raw: dict[str, Any] | None) -> NotificationPrefs:
    """Stored JSON → validated prefs with defaults for missing keys."""
    return NotificationPrefs.model_validate(raw or {})


def merge_prefs(current: dict[str, Any] | None, patch: dict[str, Any]) -> NotificationPrefs:
    """Apply a partial update (per-type channel changes merge onto the current values)."""
    base = parse_prefs(current).model_dump(mode="json")
    if "do_not_disturb" in patch:
        base["do_not_disturb"] = {**base["do_not_disturb"], **(patch["do_not_disturb"] or {})}
    types_patch: dict[str, dict[str, Any] | None] = patch.get("types") or {}
    for name, channels in types_patch.items():
        base["types"][name] = {**base["types"].get(name, {}), **(channels or {})}
    unknown = set(patch) - {"do_not_disturb", "types"}
    if unknown:
        raise DomainError("VALIDATION_ERROR", f"Unknown preference keys: {sorted(unknown)}")
    try:
        return NotificationPrefs.model_validate(base)
    except ValidationError as exc:
        raise DomainError(
            "VALIDATION_ERROR",
            "Invalid notification preferences",
            {"errors": [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]},
        ) from exc
