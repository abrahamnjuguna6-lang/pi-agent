"""T3.3: transactional outbox — commit coupling, exactly-once claim, retry/backoff, dead-letter."""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.db import models as m
from lifeos.db.session import system_session
from lifeos.domain.clock import FrozenClock, SystemClock
from lifeos.events.consumer import MAX_ATTEMPTS, OutboxConsumer, backoff
from lifeos.events.outbox import publish
from tests import factories as f

Maker = async_sessionmaker[AsyncSession]


@pytest.fixture
async def t0(db: Maker) -> datetime:
    """Frozen-clock origin taken from the DATABASE clock at test start.

    Events get `available_at = now()` from PostgreSQL and the consumer compares it with its injected
    clock, so the frozen clock must start at (or just after) the DB's current time.
    """
    async with system_session(sessionmaker=db) as s:
        return (await s.execute(select(func.now()))).scalar_one() + timedelta(seconds=1)


async def user(db: Maker) -> m.User:
    async with system_session(sessionmaker=db) as s:
        return await f.make_user(s)


async def event_count(db: Maker) -> int:
    async with system_session(sessionmaker=db) as s:
        return (await s.execute(select(func.count()).select_from(m.DomainEvent))).scalar_one()


async def test_event_commits_with_the_state_change(db: Maker) -> None:
    u = await user(db)
    async with system_session(sessionmaker=db) as s:
        await f.make_goal_tree(s, u)
        await publish(s, "test.event", u.id, {"n": 1})
    assert await event_count(db) == 1


async def test_event_discarded_when_transaction_rolls_back(db: Maker) -> None:
    u = await user(db)
    with pytest.raises(RuntimeError):
        async with system_session(sessionmaker=db) as s:
            await publish(s, "test.event", u.id, {"n": 1})
            raise RuntimeError("business write failed")
    assert await event_count(db) == 0


async def test_handler_runs_and_event_marked_processed(db: Maker, t0: datetime) -> None:
    u = await user(db)
    seen: list[dict] = []

    async def handler(session: AsyncSession, event: m.DomainEvent) -> None:
        seen.append(event.payload)

    async with system_session(sessionmaker=db) as s:
        for n in range(3):
            await publish(s, "test.event", u.id, {"n": n})
    consumer = OutboxConsumer(db, FrozenClock(t0), {"test.event": [handler]})
    result = await consumer.drain()
    assert result.processed == 3
    assert [p["n"] for p in seen] == [0, 1, 2]  # FIFO
    assert await consumer.drain() == type(result)()  # idle: nothing left


async def test_two_concurrent_consumers_process_each_event_once(db: Maker) -> None:
    u = await user(db)
    async with system_session(sessionmaker=db) as s:
        for n in range(40):
            await publish(s, "test.event", u.id, {"n": n})
    seen: list[int] = []

    async def slow_handler(session: AsyncSession, event: m.DomainEvent) -> None:
        await asyncio.sleep(0.005)
        seen.append(event.payload["n"])

    a = OutboxConsumer(db, SystemClock(), {"test.event": [slow_handler]})
    b = OutboxConsumer(db, SystemClock(), {"test.event": [slow_handler]})
    results = await asyncio.gather(a.drain(), b.drain())
    assert sorted(seen) == list(range(40))  # each exactly once
    assert results[0].processed > 0
    assert results[1].processed > 0  # both consumers did real work
    assert results[0].processed + results[1].processed == 40


async def test_failed_handler_retries_with_backoff_without_blocking_others(db: Maker, t0: datetime) -> None:
    u = await user(db)
    async with system_session(sessionmaker=db) as s:
        await publish(s, "test.event", u.id, {"n": "poison"})
        await publish(s, "test.event", u.id, {"n": "ok"})
    calls: list[str] = []

    async def handler(session: AsyncSession, event: m.DomainEvent) -> None:
        calls.append(event.payload["n"])
        if event.payload["n"] == "poison":
            session.add(m.Goal(user_id=u.id, title="side effect", category="Life"))  # must roll back
            await session.flush()
            raise ValueError("handler bug")

    clock = FrozenClock(t0)
    consumer = OutboxConsumer(db, clock, {"test.event": [handler]})
    result = await consumer.drain()
    assert (result.processed, result.failed) == (1, 1)
    assert calls == ["poison", "ok"]  # the failing event did not block the next one

    async with system_session(sessionmaker=db) as s:
        poison = (await s.execute(select(m.DomainEvent).where(m.DomainEvent.attempts == 1))).scalar_one()
        goals = (await s.execute(select(func.count()).select_from(m.Goal))).scalar_one()
    assert poison.last_error is not None
    assert poison.last_error.startswith("ValueError: handler bug")
    assert poison.available_at == t0 + backoff(1)
    assert goals == 0  # the handler's partial write was rolled back

    assert (await consumer.drain()).failed == 0  # still backing off
    clock.set(t0 + backoff(1))
    assert (await consumer.drain()).failed == 1  # retried once the backoff elapsed


async def test_dead_letter_after_max_attempts_raises_alert(db: Maker, t0: datetime) -> None:
    u = await user(db)
    async with system_session(sessionmaker=db) as s:
        await publish(s, "test.event", u.id, {})

    async def always_fails(session: AsyncSession, event: m.DomainEvent) -> None:
        raise RuntimeError("permanent")

    clock = FrozenClock(t0)
    consumer = OutboxConsumer(db, clock, {"test.event": [always_fails]})
    outcomes = []
    for _ in range(MAX_ATTEMPTS):
        outcomes.append(await consumer.process_one())
        clock.set(clock.now() + timedelta(hours=2))
    assert outcomes[-1] == "dead"
    assert outcomes[:-1] == ["failed"] * (MAX_ATTEMPTS - 1)
    assert await consumer.process_one() is None  # dead events are never claimed again

    async with system_session(sessionmaker=db) as s:
        alert = (await s.execute(select(m.DeveloperAlert))).scalar_one()
    assert alert.source == "outbox"
    assert alert.details["error"] == "RuntimeError: permanent"


async def test_events_without_handlers_are_marked_processed(db: Maker, t0: datetime) -> None:
    u = await user(db)
    async with system_session(sessionmaker=db) as s:
        await publish(s, "test.event", u.id, {})
    assert (await OutboxConsumer(db, FrozenClock(t0)).drain()).processed == 1


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff(n).total_seconds() for n in (1, 2, 3)] == [2, 4, 8]
    assert backoff(30) == timedelta(hours=1)
