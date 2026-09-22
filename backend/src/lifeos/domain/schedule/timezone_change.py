"""Timezone change handling (design §12.5, R21.4).

Every active, not-yet-started Planned Daily Action keeps its local calendar date and wall-clock time; its
UTC instants are recomputed in the new zone (DST policy of §20.3) with a `timezone_change` history row.
Past actions, started/finished actions and all Check-in timestamps are never touched. Runs inside the
profile update's transaction (ProfileService timezone hook).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.schedule.common import add_history
from lifeos.domain.timeutil import resolve_local, to_local


class TimezoneChangeService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def __call__(self, s: AsyncSession, user_id: uuid.UUID, old_tz: str, new_tz: str) -> None:
        now = self._clock.now()
        future = (
            await s.execute(
                select(m.DailyAction)
                .where(
                    m.DailyAction.user_id == user_id,
                    m.DailyAction.lifecycle_state == "active",
                    m.DailyAction.status == "Planned",
                    m.DailyAction.scheduled_start > now,
                )
                .with_for_update()
            )
        ).scalars()
        for action in future:
            start_local = to_local(action.scheduled_start, old_tz)
            end_local = to_local(action.scheduled_end, old_tz)
            new_start = resolve_local(start_local.date(), start_local.time(), new_tz)
            new_end = resolve_local(end_local.date(), end_local.time(), new_tz)
            if new_end <= new_start:  # only possible when both ends fall in one DST gap
                new_end = new_start + (action.scheduled_end - action.scheduled_start)
            if (new_start, new_end) == (action.scheduled_start, action.scheduled_end):
                continue
            add_history(
                s, action, "timezone_change", new_start, new_end, "System", f"{old_tz} → {new_tz}", now
            )
            action.scheduled_start, action.scheduled_end = new_start, new_end
            action.updated_at = now
        await s.flush()
