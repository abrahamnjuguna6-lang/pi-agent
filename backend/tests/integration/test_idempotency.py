"""T3.2: two-phase idempotency claim (design §22, R15.5)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.api.deps import ContainerDep, CurrentUser
from lifeos.api.errors import envelope
from lifeos.api.idempotency import IdempotencyKey, idempotent
from lifeos.db import models as m
from lifeos.db.session import system_session
from lifeos.domain.clock import FrozenClock
from lifeos.domain.errors import DomainError
from lifeos.domain.idempotency import STALE_AFTER, IdempotencyService, StoredResult, fingerprint
from tests import factories as f
from tests.integration.api_harness import Api

Maker = async_sessionmaker[AsyncSession]
T0 = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
FP = fingerprint("POST", "/goals", {"title": "Run a marathon"})


async def make_user(db: Maker) -> m.User:
    async with system_session(sessionmaker=db) as s:
        return await f.make_user(s)


async def goal_count(db: Maker, user_id: uuid.UUID) -> int:
    async with system_session(sessionmaker=db) as s:
        return (await s.execute(select(func.count()).where(m.Goal.user_id == user_id))).scalar_one()


def create_goal_op(user_id: uuid.UUID, calls: list[int], delay: float = 0.0):
    async def op(s: AsyncSession) -> StoredResult:
        calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        goal = f.GoalFactory(user_id=user_id)
        await f.persist(s, goal)
        return StoredResult(201, {"goal_id": str(goal.id)})

    return op


async def test_duplicate_after_success_returns_stored_result(db: Maker) -> None:
    u = await make_user(db)
    svc = IdempotencyService(db, FrozenClock(T0))
    calls: list[int] = []
    first = await svc.run(u.id, "key-00000001", FP, create_goal_op(u.id, calls))
    second = await svc.run(u.id, "key-00000001", FP, create_goal_op(u.id, calls))
    assert first.replayed is False
    assert second.replayed is True
    assert second.result == first.result
    assert len(calls) == 1
    assert await goal_count(db, u.id) == 1


async def test_concurrent_duplicates_execute_exactly_once(db: Maker) -> None:
    u = await make_user(db)
    svc = IdempotencyService(db, FrozenClock(T0))
    calls: list[int] = []

    async def attempt() -> str:
        try:
            outcome = await svc.run(u.id, "race-key-001", FP, create_goal_op(u.id, calls, delay=0.05))
            return "replayed" if outcome.replayed else "executed"
        except DomainError as exc:
            return exc.code

    outcomes = await asyncio.gather(*(attempt() for _ in range(10)))
    assert outcomes.count("executed") == 1
    assert set(outcomes) <= {"executed", "replayed", "REQUEST_IN_PROGRESS"}
    assert len(calls) == 1
    assert await goal_count(db, u.id) == 1


async def test_same_key_different_payload_rejected(db: Maker) -> None:
    u = await make_user(db)
    svc = IdempotencyService(db, FrozenClock(T0))
    await svc.run(u.id, "key-00000002", FP, create_goal_op(u.id, []))
    other = fingerprint("POST", "/goals", {"title": "Something else"})
    with pytest.raises(DomainError) as exc:
        await svc.run(u.id, "key-00000002", other, create_goal_op(u.id, []))
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"


async def test_crash_after_business_write_rolls_back_and_retry_executes_once(db: Maker) -> None:
    u = await make_user(db)
    svc = IdempotencyService(db, FrozenClock(T0))

    async def crashing(s: AsyncSession) -> StoredResult:
        await f.persist(s, f.GoalFactory(user_id=u.id))  # business write happens…
        raise RuntimeError("process died before commit")  # …then the transaction aborts

    with pytest.raises(RuntimeError):
        await svc.run(u.id, "key-00000003", FP, crashing)
    assert await goal_count(db, u.id) == 0  # nothing committed

    calls: list[int] = []
    outcome = await svc.run(u.id, "key-00000003", FP, create_goal_op(u.id, calls))
    assert outcome.replayed is False
    assert len(calls) == 1
    assert await goal_count(db, u.id) == 1


async def test_stale_processing_record_is_reclaimed(db: Maker) -> None:
    u = await make_user(db)
    clock = FrozenClock(T0)
    svc = IdempotencyService(db, clock)
    async with system_session(sessionmaker=db) as s:  # a worker died mid-request, leaving "processing"
        s.add(
            m.IdempotencyRecord(
                user_id=u.id,
                idempotency_key="key-00000004",
                request_fingerprint=FP,
                status="processing",
                created_at=T0,
                updated_at=T0,
                expires_at=T0 + timedelta(hours=24),
            )
        )
    with pytest.raises(DomainError) as exc:
        await svc.run(u.id, "key-00000004", FP, create_goal_op(u.id, []))
    assert exc.value.code == "REQUEST_IN_PROGRESS"  # still fresh

    clock.set(T0 + STALE_AFTER)
    calls: list[int] = []
    assert (await svc.run(u.id, "key-00000004", FP, create_goal_op(u.id, calls))).replayed is False
    assert len(calls) == 1


async def test_key_is_treated_as_new_after_24_hours(db: Maker) -> None:
    u = await make_user(db)
    clock = FrozenClock(T0)
    svc = IdempotencyService(db, clock)
    await svc.run(u.id, "key-00000005", FP, create_goal_op(u.id, []))
    clock.set(T0 + timedelta(hours=24))
    other = fingerprint("POST", "/goals", {"title": "new request, reused key"})
    outcome = await svc.run(u.id, "key-00000005", other, create_goal_op(u.id, []))
    assert outcome.replayed is False
    assert await goal_count(db, u.id) == 2


async def test_keys_are_scoped_per_user(db: Maker) -> None:
    a, b = await make_user(db), await make_user(db)
    svc = IdempotencyService(db, FrozenClock(T0))
    await svc.run(a.id, "shared-key-01", FP, create_goal_op(a.id, []))
    outcome = await svc.run(b.id, "shared-key-01", FP, create_goal_op(b.id, []))
    assert outcome.replayed is False  # another user's key never replays across tenants


async def test_failed_record_marked_and_retryable(db: Maker) -> None:
    u = await make_user(db)
    svc = IdempotencyService(db, FrozenClock(T0))

    async def invalid(s: AsyncSession) -> StoredResult:
        raise DomainError("VALIDATION_ERROR", "bad input")

    with pytest.raises(DomainError):
        await svc.run(u.id, "key-00000006", FP, invalid)
    async with system_session(sessionmaker=db) as s:
        status = (
            await s.execute(
                select(m.IdempotencyRecord.status).where(
                    m.IdempotencyRecord.idempotency_key == "key-00000006"
                )
            )
        ).scalar_one()
    assert status == "failed"
    assert (await svc.run(u.id, "key-00000006", FP, create_goal_op(u.id, []))).replayed is False


# --------------------------------------------------------------------------- HTTP binding


class GoalIn(BaseModel):
    title: str


def goal_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/_test/goals", status_code=201)
    async def create_goal(
        body: GoalIn, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
    ) -> JSONResponse:
        async def op(s: AsyncSession) -> StoredResult:
            goal = f.GoalFactory(user_id=user.user_id, title=body.title)
            await f.persist(s, goal)
            return StoredResult(201, envelope(request, {"id": str(goal.id), "title": goal.title}))

        return await idempotent(request, c, user, key, body.model_dump(mode="json"), op)

    return router


async def test_http_requires_key_and_replays(api: Api) -> None:
    api.client._transport.app.include_router(goal_router())  # type: ignore[attr-defined]
    await api.verified_user("dora@example.com")
    auth = {"Authorization": f"Bearer {(await api.tokens('dora@example.com'))['access_token']}"}

    missing = await api.client.post("/api/v1/_test/goals", json={"title": "x"}, headers=auth)
    assert (missing.status_code, missing.json()["error"]["code"]) == (422, "VALIDATION_ERROR")

    headers = {**auth, "Idempotency-Key": "client-key-0001"}
    first = await api.client.post("/api/v1/_test/goals", json={"title": "Read 12 books"}, headers=headers)
    second = await api.client.post("/api/v1/_test/goals", json={"title": "Read 12 books"}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["data"] == second.json()["data"]
    assert second.headers["idempotent-replayed"] == "true"

    reused = await api.client.post("/api/v1/_test/goals", json={"title": "different"}, headers=headers)
    assert (reused.status_code, reused.json()["error"]["code"]) == (422, "IDEMPOTENCY_KEY_REUSED")

    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        assert (await s.execute(select(func.count()).select_from(m.Goal))).scalar_one() == 1
