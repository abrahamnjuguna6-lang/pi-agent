"""Idempotent execution of mutations (design §22, R15.5).

Two-phase claim — the claim must be visible to concurrent duplicates BEFORE the operation runs:

  Phase 1 (own transaction, committed):  INSERT … ON CONFLICT DO NOTHING RETURNING
      new row           → proceed
      existing row      → fingerprint differs → IDEMPOTENCY_KEY_REUSED (422)
                           completed & unexpired → return the stored result (no re-execution)
                           processing & fresh    → REQUEST_IN_PROGRESS (409)
                           processing & stale / failed / expired → reclaim atomically, proceed
  Phase 2 (one transaction): business writes + `status='completed', result=…` commit together.
      On exception: rollback, then mark `failed` in a separate transaction and re-raise.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.db import models as m
from lifeos.db.session import user_session
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError

RETENTION = timedelta(hours=24)
STALE_AFTER = timedelta(seconds=60)


@dataclass(frozen=True)
class StoredResult:
    """What a mutation returns, persisted so duplicates can be answered verbatim."""

    status_code: int
    body: Any


@dataclass(frozen=True)
class IdempotentOutcome:
    result: StoredResult
    replayed: bool  # True when returned from a previous execution


def fingerprint(method: str, route: str, body: Any) -> bytes:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{method.upper()} {route}\n{canonical}".encode()).digest()


Operation = Callable[[AsyncSession], Awaitable[StoredResult]]


class IdempotencyService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._maker = sessionmaker
        self._clock = clock

    async def run(
        self, user_id: uuid.UUID, key: str, request_fingerprint: bytes, op: Operation
    ) -> IdempotentOutcome:
        replay = await self._claim(user_id, key, request_fingerprint)
        if replay is not None:
            return IdempotentOutcome(replay, replayed=True)
        try:
            async with user_session(user_id, sessionmaker=self._maker) as s:
                result = await op(s)
                await s.execute(
                    update(m.IdempotencyRecord)
                    .where(m.IdempotencyRecord.user_id == user_id, m.IdempotencyRecord.idempotency_key == key)
                    .values(
                        status="completed",
                        result={"status_code": result.status_code, "body": result.body},
                        updated_at=self._clock.now(),
                    )
                )
        except BaseException:
            await self._mark_failed(user_id, key)
            raise
        return IdempotentOutcome(result, replayed=False)

    async def _claim(self, user_id: uuid.UUID, key: str, fp: bytes) -> StoredResult | None:
        now = self._clock.now()
        async with user_session(user_id, sessionmaker=self._maker) as s:
            inserted = (
                await s.execute(
                    insert(m.IdempotencyRecord)
                    .values(
                        user_id=user_id,
                        idempotency_key=key,
                        request_fingerprint=fp,
                        status="processing",
                        created_at=now,
                        updated_at=now,
                        expires_at=now + RETENTION,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
                    .returning(m.IdempotencyRecord.idempotency_key)
                )
            ).scalar_one_or_none()
            if inserted is not None:
                return None

            record = (
                await s.execute(
                    select(m.IdempotencyRecord).where(
                        m.IdempotencyRecord.user_id == user_id, m.IdempotencyRecord.idempotency_key == key
                    )
                )
            ).scalar_one()
            expired = record.expires_at <= now
            if not expired and record.request_fingerprint != fp:
                raise DomainError(
                    "IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was already used for a different request"
                )
            if not expired and record.status == "completed":
                stored = record.result or {}
                return StoredResult(int(stored.get("status_code", 200)), stored.get("body"))
            if not expired and record.status == "processing" and record.updated_at > now - STALE_AFTER:
                raise DomainError("REQUEST_IN_PROGRESS", "An identical request is still being processed")

            if not await self._reclaim(s, user_id, key, fp, now):
                raise DomainError("REQUEST_IN_PROGRESS", "An identical request is still being processed")
            return None

    async def _reclaim(self, s: AsyncSession, user_id: uuid.UUID, key: str, fp: bytes, now: datetime) -> bool:
        """Atomically take over a failed, stale-processing or expired record (only one caller wins)."""
        won = (
            await s.execute(
                update(m.IdempotencyRecord)
                .where(
                    m.IdempotencyRecord.user_id == user_id,
                    m.IdempotencyRecord.idempotency_key == key,
                    or_(
                        m.IdempotencyRecord.status == "failed",
                        (m.IdempotencyRecord.status == "processing")
                        & (m.IdempotencyRecord.updated_at <= now - STALE_AFTER),
                        m.IdempotencyRecord.expires_at <= now,
                    ),
                )
                .values(
                    status="processing",
                    request_fingerprint=fp,
                    result=None,
                    created_at=now,
                    updated_at=now,
                    expires_at=now + RETENTION,
                )
                .returning(m.IdempotencyRecord.idempotency_key)
            )
        ).scalar_one_or_none()
        return won is not None

    async def _mark_failed(self, user_id: uuid.UUID, key: str) -> None:
        async with user_session(user_id, sessionmaker=self._maker) as s:
            await s.execute(
                update(m.IdempotencyRecord)
                .where(
                    m.IdempotencyRecord.user_id == user_id,
                    m.IdempotencyRecord.idempotency_key == key,
                    m.IdempotencyRecord.status == "processing",
                )
                .values(status="failed", updated_at=self._clock.now())
            )
