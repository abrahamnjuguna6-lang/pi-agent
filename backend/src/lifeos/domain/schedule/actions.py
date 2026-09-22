"""Daily Action reads and manual creation (R3.8, design §12.1 MANUAL source)."""

from __future__ import annotations

import uuid
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.schedule.common import get_action, user_timezone
from lifeos.domain.timeutil import block_instants, local_date


async def insert_action_with_initial_checkin(
    s: AsyncSession, action: m.DailyAction, clock: Clock
) -> m.DailyAction:
    """Creation and the initial `none → Planned` check-in happen in one transaction (design §16.2)."""
    s.add(action)
    await s.flush()
    s.add(
        m.CheckinRecord(
            daily_action_id=action.id,
            user_id=action.user_id,
            previous_status=None,
            new_status="Planned",
            transition_source="System",
            created_at=clock.now(),
        )
    )
    await s.flush()
    return action


class DailyActionService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def list_day(
        self, s: AsyncSession, user_id: uuid.UUID, day: date | None, include_cancelled: bool = False
    ) -> tuple[date, list[m.DailyAction]]:
        """Unified day view ordered by scheduled start (R3.8). `day` defaults to the user's today."""
        tz = await user_timezone(s, user_id)
        target = day or local_date(self._clock.now(), tz)
        query = select(m.DailyAction).where(m.DailyAction.user_id == user_id, m.DailyAction.date == target)
        if not include_cancelled:
            query = query.where(m.DailyAction.lifecycle_state == "active")
        rows = (await s.execute(query.order_by(m.DailyAction.scheduled_start, m.DailyAction.id))).scalars()
        return target, list(rows)

    async def get(self, s: AsyncSession, user_id: uuid.UUID, action_id: uuid.UUID) -> m.DailyAction:
        return await get_action(s, user_id, action_id)

    async def checkins(
        self, s: AsyncSession, user_id: uuid.UUID, action_id: uuid.UUID
    ) -> list[m.CheckinRecord]:
        from lifeos.domain.checkins import chain_order

        await get_action(s, user_id, action_id)
        rows = (
            await s.execute(select(m.CheckinRecord).where(m.CheckinRecord.daily_action_id == action_id))
        ).scalars()
        return chain_order(list(rows))

    async def create_manual(
        self, s: AsyncSession, user_id: uuid.UUID, title: str, day: date, start: time, end: time
    ) -> m.DailyAction:
        tz = await user_timezone(s, user_id)
        begin, finish = block_instants(day, start, end, tz)
        now = self._clock.now()
        action = m.DailyAction(
            id=uuid.uuid4(),
            user_id=user_id,
            title=title,
            date=day,
            occurrence_date=day,
            scheduled_start=begin,
            scheduled_end=finish,
            status="Planned",
            source_type="MANUAL",
            source_id=None,
            created_at=now,
            updated_at=now,
        )
        return await insert_action_with_initial_checkin(s, action, self._clock)
