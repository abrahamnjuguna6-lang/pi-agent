"""Outbox consumer: at-least-once delivery to idempotent handlers (design §4.3).

Each event is processed in its OWN transaction:
  claim (FOR UPDATE SKIP LOCKED) → run every handler for its type (same session) → mark processed.
A handler failure rolls back that event only; the failure is recorded in a separate transaction with
exponential backoff (`available_at`), and after MAX_ATTEMPTS the event is dead-lettered with a
developer alert. Concurrent consumers never process the same event at the same time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.db import models as m
from lifeos.db.session import system_session
from lifeos.domain.clock import Clock

log = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, m.DomainEvent], Awaitable[None]]
MAX_ATTEMPTS = 10
MAX_BACKOFF = timedelta(hours=1)


def backoff(attempts: int) -> timedelta:
    """2^attempts seconds, capped at one hour."""
    return min(timedelta(seconds=2**attempts), MAX_BACKOFF)


@dataclass(frozen=True)
class BatchResult:
    processed: int = 0
    failed: int = 0
    dead_lettered: int = 0


class OutboxConsumer:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Clock,
        handlers: dict[str, list[Handler]] | None = None,
    ) -> None:
        self._maker = sessionmaker
        self._clock = clock
        self._handlers: dict[str, list[Handler]] = {k: list(v) for k, v in (handlers or {}).items()}

    def register(self, event_type: str, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def process_one(self) -> str | None:
        """Process the next available event. Returns 'processed' | 'failed' | 'dead' | None if idle."""
        now = self._clock.now()
        event_id: int | None = None
        try:
            async with system_session(sessionmaker=self._maker) as s:
                event = (
                    await s.execute(
                        select(m.DomainEvent)
                        .where(
                            m.DomainEvent.processed_at.is_(None),
                            m.DomainEvent.dead_lettered_at.is_(None),
                            m.DomainEvent.available_at <= now,
                        )
                        .order_by(m.DomainEvent.id)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if event is None:
                    return None
                event_id = event.id
                for handler in self._handlers.get(event.event_type, []):
                    await handler(s, event)
                event.processed_at = now
            return "processed"
        except Exception as exc:
            if event_id is None:
                raise
            return await self._record_failure(event_id, exc)

    async def _record_failure(self, event_id: int, exc: Exception) -> str:
        now = self._clock.now()
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        async with system_session(sessionmaker=self._maker) as s:
            event = (await s.execute(select(m.DomainEvent).where(m.DomainEvent.id == event_id))).scalar_one()
            attempts = event.attempts + 1
            if attempts >= MAX_ATTEMPTS:
                await s.execute(
                    update(m.DomainEvent)
                    .where(m.DomainEvent.id == event_id)
                    .values(attempts=attempts, last_error=error, dead_lettered_at=now)
                )
                s.add(
                    m.DeveloperAlert(
                        source="outbox",
                        severity="error",
                        user_id=event.user_id,
                        details={"event_id": event_id, "event_type": event.event_type, "error": error},
                    )
                )
                log.error("outbox event %s dead-lettered after %s attempts", event_id, attempts)
                return "dead"
            await s.execute(
                update(m.DomainEvent)
                .where(m.DomainEvent.id == event_id)
                .values(attempts=attempts, last_error=error, available_at=now + backoff(attempts))
            )
        log.warning("outbox event %s failed (attempt %s): %s", event_id, attempts, error)
        return "failed"

    async def drain(self, max_events: int = 1000) -> BatchResult:
        """Process available events until idle or `max_events` reached."""
        processed = failed = dead = 0
        for _ in range(max_events):
            outcome = await self.process_one()
            if outcome is None:
                break
            processed += outcome == "processed"
            failed += outcome == "failed"
            dead += outcome == "dead"
        return BatchResult(processed, failed, dead)

    async def run_forever(self, idle_sleep_s: float = 0.5) -> None:  # pragma: no cover - worker loop
        while True:
            result = await self.drain(max_events=200)
            if result.processed + result.failed + result.dead_lettered == 0:
                await asyncio.sleep(idle_sleep_s)
