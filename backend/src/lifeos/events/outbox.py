"""Transactional outbox publisher (design §4.3).

`publish()` inserts into `domain_events` using the CALLER's session, so the event commits or rolls
back together with the state change that caused it. Nothing is sent anywhere until a consumer
claims it after commit.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.events.types import EventType


async def publish(
    session: AsyncSession, event_type: EventType, user_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    session.add(m.DomainEvent(user_id=user_id, event_type=event_type, payload=payload))
    await session.flush()
