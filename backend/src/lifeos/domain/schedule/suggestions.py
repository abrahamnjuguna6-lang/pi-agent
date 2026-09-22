"""Late-completion schedule suggestions (design §19.9, R2.9).

When an action is completed after its scheduled end, the following Planned actions of that day are
shifted by the overrun (order and durations preserved). Actions that would end after the User's sleep
time are proposed for tomorrow instead. Nothing changes until the User accepts (R2.9, R20.4).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.schedule.reschedule import RescheduleService
from lifeos.domain.timeutil import local_date, resolve_local, to_local

MIN_OVERRUN = timedelta(minutes=1)
SuggestionHook = Callable[[AsyncSession, m.ScheduleSuggestion], Awaitable[None]]


@dataclass(frozen=True)
class Following:
    action_id: uuid.UUID
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Shift:
    action_id: uuid.UUID
    new_start: datetime
    new_end: datetime
    move_to_tomorrow: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "daily_action_id": str(self.action_id),
            "new_start": self.new_start.isoformat(),
            "new_end": self.new_end.isoformat(),
            "move_to_tomorrow": self.move_to_tomorrow,
        }


def build_proposal(
    overrun: timedelta, following: list[Following], sleep_limit: datetime | None
) -> list[Shift]:
    """Pure: shift each following action by the overrun; past the sleep limit → same time tomorrow."""
    shifts: list[Shift] = []
    for item in sorted(following, key=lambda f: (f.start, f.action_id)):
        new_start, new_end = item.start + overrun, item.end + overrun
        if sleep_limit is not None and new_end > sleep_limit:
            shifts.append(
                Shift(item.action_id, item.start + timedelta(days=1), item.end + timedelta(days=1), True)
            )
        else:
            shifts.append(Shift(item.action_id, new_start, new_end, False))
    return shifts


def sleep_limit_for(day: date, sleep_time: time | None, wake_time: time | None, tz: str) -> datetime | None:
    """Local sleep time on `day`; a sleep time before wake time (or noon) means after midnight."""
    if sleep_time is None:
        return None
    pivot = wake_time or time(12, 0)
    sleep_day = day + timedelta(days=1) if sleep_time <= pivot else day
    return resolve_local(sleep_day, sleep_time, tz)


class SuggestionService:
    def __init__(
        self, clock: Clock, reschedule: RescheduleService, hooks: list[SuggestionHook] | None = None
    ) -> None:
        self._clock = clock
        self._reschedule = reschedule
        self._hooks = list(hooks or [])  # T13.1: create the `schedule_suggestion` proactive flag

    async def on_status_changed(self, s: AsyncSession, event: m.DomainEvent) -> None:
        """Outbox handler for `daily_action.status_changed`."""
        payload = event.payload
        if payload.get("new_status") != "Completed" or not payload.get("completed_at"):
            return
        completed_at = datetime.fromisoformat(payload["completed_at"])
        scheduled_end = datetime.fromisoformat(payload["scheduled_end"])
        overrun = completed_at - scheduled_end
        if overrun < MIN_OVERRUN:
            return
        trigger_id = uuid.UUID(payload["daily_action_id"])
        day = date.fromisoformat(payload["date"])
        user = (await s.execute(select(m.User).where(m.User.id == event.user_id))).scalar_one()
        following = [
            Following(a.id, a.scheduled_start, a.scheduled_end)
            for a in (
                await s.execute(
                    select(m.DailyAction).where(
                        m.DailyAction.user_id == event.user_id,
                        m.DailyAction.date == day,
                        m.DailyAction.id != trigger_id,
                        m.DailyAction.lifecycle_state == "active",
                        m.DailyAction.status == "Planned",
                        m.DailyAction.scheduled_start >= scheduled_end,
                    )
                )
            ).scalars()
        ]
        if not following:
            return
        shifts = build_proposal(
            overrun, following, sleep_limit_for(day, user.sleep_time, user.wake_time, user.timezone)
        )
        suggestion_id = (
            await s.execute(
                insert(m.ScheduleSuggestion)
                .values(
                    user_id=event.user_id,
                    trigger_action_id=trigger_id,
                    local_date=day,
                    proposal=[x.as_json() for x in shifts],
                    created_at=self._clock.now(),
                )
                .on_conflict_do_nothing(index_elements=["trigger_action_id"])  # idempotent handler
                .returning(m.ScheduleSuggestion.id)
            )
        ).scalar_one_or_none()
        if suggestion_id is None:
            return
        suggestion = (
            await s.execute(select(m.ScheduleSuggestion).where(m.ScheduleSuggestion.id == suggestion_id))
        ).scalar_one()
        for hook in self._hooks:
            await hook(s, suggestion)

    async def _get(
        self, s: AsyncSession, user_id: uuid.UUID, suggestion_id: uuid.UUID
    ) -> m.ScheduleSuggestion:
        row = (
            await s.execute(
                select(m.ScheduleSuggestion)
                .where(m.ScheduleSuggestion.id == suggestion_id, m.ScheduleSuggestion.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("schedule suggestion")
        return row

    async def list_suggestions(
        self, s: AsyncSession, user_id: uuid.UUID, status: str | None = None
    ) -> list[m.ScheduleSuggestion]:
        query = select(m.ScheduleSuggestion).where(m.ScheduleSuggestion.user_id == user_id)
        if status is not None:
            query = query.where(m.ScheduleSuggestion.status == status)
        return list((await s.execute(query.order_by(m.ScheduleSuggestion.created_at.desc()))).scalars())

    async def _decidable(
        self, s: AsyncSession, user_id: uuid.UUID, suggestion_id: uuid.UUID
    ) -> m.ScheduleSuggestion:
        suggestion = await self._get(s, user_id, suggestion_id)
        if suggestion.status != "proposed":
            raise DomainError("INVALID_TRANSITION", f"This suggestion was already {suggestion.status}")
        user_tz = (await s.execute(select(m.User.timezone).where(m.User.id == user_id))).scalar_one()
        if suggestion.local_date < local_date(self._clock.now(), user_tz):
            suggestion.status = "expired"
            suggestion.decided_at = self._clock.now()
            raise DomainError("INVALID_TRANSITION", "This suggestion has expired")
        return suggestion

    async def accept(
        self, s: AsyncSession, user_id: uuid.UUID, suggestion_id: uuid.UUID
    ) -> m.ScheduleSuggestion:
        suggestion = await self._decidable(s, user_id, suggestion_id)
        tz = (await s.execute(select(m.User.timezone).where(m.User.id == user_id))).scalar_one()
        for item in suggestion.proposal:
            action = (
                await s.execute(
                    select(m.DailyAction).where(m.DailyAction.id == uuid.UUID(item["daily_action_id"]))
                )
            ).scalar_one_or_none()
            if action is None or action.lifecycle_state != "active" or action.status != "Planned":
                continue  # the User already acted on it; never override that
            start = to_local(datetime.fromisoformat(item["new_start"]), tz)
            end = to_local(datetime.fromisoformat(item["new_end"]), tz)
            await self._reschedule.reschedule(
                s,
                user_id,
                action.id,
                start.date(),
                start.time(),
                end.time(),
                "User",
                "late_completion_suggestion",
            )
        suggestion.status = "accepted"
        suggestion.decided_at = self._clock.now()
        await s.flush()
        return suggestion

    async def reject(
        self, s: AsyncSession, user_id: uuid.UUID, suggestion_id: uuid.UUID
    ) -> m.ScheduleSuggestion:
        suggestion = await self._decidable(s, user_id, suggestion_id)
        suggestion.status = "rejected"
        suggestion.decided_at = self._clock.now()
        await s.flush()
        return suggestion
