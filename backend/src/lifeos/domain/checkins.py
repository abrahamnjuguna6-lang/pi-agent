"""Daily Action status machine and Check-in Records (design §16.2, R3.1–3.6, R3.13).

Valid transitions (anything else is rejected and produces NO Check-in Record):
    Planned → Started | Completed | Skipped
    Started → Completed | Skipped
Skipped requires a non-empty reason (R3.6); Completed records the completion time (R3.5).
The status update, the Check-in Record, same-transaction sync hooks (Task / Habit) and the
`daily_action.status_changed` outbox event all commit together — or not at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError
from lifeos.domain.schedule.common import get_action, goal_is_archived, source_goal_id
from lifeos.events.outbox import publish

Status = Literal["Planned", "Started", "Completed", "Skipped"]
TransitionSource = Literal["User", "Agent", "System"]

TRANSITIONS: dict[str, frozenset[str]] = {
    "Planned": frozenset({"Started", "Completed", "Skipped"}),
    "Started": frozenset({"Completed", "Skipped"}),
    "Completed": frozenset(),
    "Skipped": frozenset(),
}
NOTE_MAX = 2000


@dataclass(frozen=True)
class TransitionContext:
    """Extra intent carried to hooks, e.g. a Habit partial completion (design §17.2)."""

    habit_result: Literal["completed", "partial", "skipped"] | None = None
    completion_percent: int | None = None


TransitionHook = Callable[
    [AsyncSession, m.DailyAction, str, str, str | None, TransitionContext], Awaitable[None]
]


def validate_transition(current: str, new: str, lifecycle_state: str, note: str | None) -> str | None:
    """Pure rule check. Returns the normalized note, or raises DomainError."""
    if lifecycle_state != "active":
        raise DomainError("INVALID_TRANSITION", "This daily action was cancelled", {"status": current})
    if new not in TRANSITIONS.get(current, frozenset()):
        raise DomainError(
            "INVALID_TRANSITION",
            f"Cannot change a {current} action to {new}",
            {"from": current, "to": new},
        )
    cleaned = note.strip() if note is not None else None
    if cleaned is not None and len(cleaned) > NOTE_MAX:
        raise DomainError(
            "VALIDATION_ERROR", f"Note must be at most {NOTE_MAX} characters", {"field": "note"}
        )
    if new == "Skipped" and not cleaned:
        raise DomainError("SKIP_REASON_REQUIRED", "Tell us why you skipped this", {"field": "note"})
    return cleaned or None


def chain_order(records: list[m.CheckinRecord]) -> list[m.CheckinRecord]:
    """Chronological order; records sharing a timestamp are ordered by the transition chain
    (each record's `previous_status` is the prior record's `new_status`). IDs are random UUIDs, so
    they cannot break ties."""
    ordered: list[m.CheckinRecord] = []
    by_time: dict[object, list[m.CheckinRecord]] = {}
    for record in records:
        by_time.setdefault(record.created_at, []).append(record)
    for at in sorted(by_time):  # type: ignore[type-var]
        group = list(by_time[at])
        last = ordered[-1].new_status if ordered else None
        while group:
            nxt = next((r for r in group if r.previous_status == last), group[0])
            group.remove(nxt)
            ordered.append(nxt)
            last = nxt.new_status
    return ordered


class CheckinService:
    def __init__(self, clock: Clock, hooks: list[TransitionHook] | None = None) -> None:
        self._clock = clock
        self._hooks = list(hooks or [])

    async def transition(
        self,
        s: AsyncSession,
        user_id: uuid.UUID,
        action_id: uuid.UUID,
        new_status: Status,
        note: str | None = None,
        source: TransitionSource = "User",
        trace_id: uuid.UUID | None = None,
        context: TransitionContext | None = None,
    ) -> tuple[m.DailyAction, m.CheckinRecord]:
        action = await get_action(s, user_id, action_id, lock=True)  # serializes concurrent check-ins
        cleaned = validate_transition(action.status, new_status, action.lifecycle_state, note)
        if await goal_is_archived(s, await source_goal_id(s, action)):
            raise DomainError("GOAL_ARCHIVED", "This action belongs to an archived goal and is read-only")

        now = self._clock.now()
        previous = action.status
        record = m.CheckinRecord(
            daily_action_id=action.id,
            user_id=user_id,
            previous_status=previous,
            new_status=new_status,
            transition_source=source,
            note=cleaned,
            trace_id=trace_id,
            created_at=now,
        )
        s.add(record)
        action.status = new_status
        action.updated_at = now
        if new_status == "Completed":
            action.completed_at = now
        await s.flush()

        ctx = context or TransitionContext()
        for hook in self._hooks:
            await hook(s, action, previous, new_status, cleaned, ctx)
        await publish(
            s,
            "daily_action.status_changed",
            user_id,
            {
                "daily_action_id": str(action.id),
                "previous_status": previous,
                "new_status": new_status,
                "source_type": action.source_type,
                "source_id": str(action.source_id) if action.source_id else None,
                "date": action.date.isoformat(),
                "scheduled_end": action.scheduled_end.isoformat(),
                "completed_at": action.completed_at.isoformat() if action.completed_at else None,
                "transition_source": source,
            },
            at=now,
        )
        return action, record
