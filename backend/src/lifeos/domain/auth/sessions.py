"""Session-version validation for every authenticated request (design §21.2).

PostgreSQL `auth_sessions` is authoritative; Redis caches `{ver, active}` per session id. A cache
miss always falls back to the database, so an empty or flushed Redis never admits a revoked token.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.db import models as m
from lifeos.db.session import system_session
from lifeos.domain.errors import DomainError
from lifeos.security.jwt import JwtService, TokenError

REVOCATION_CHANNEL = "auth:revoked"


@dataclass(frozen=True)
class AuthContext:
    user_id: uuid.UUID
    session_id: uuid.UUID


def _cache_key(session_id: uuid.UUID) -> str:
    return f"auth:sess:{session_id}"


class SessionCache:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def get(self, session_id: uuid.UUID) -> tuple[int, bool] | None:
        raw = await self._redis.get(_cache_key(session_id))
        if raw is None:
            return None
        data = json.loads(raw)
        return int(data["ver"]), bool(data["active"])

    async def put(self, session_id: uuid.UUID, version: int, active: bool) -> None:
        await self._redis.set(
            _cache_key(session_id), json.dumps({"ver": version, "active": active}), ex=self._ttl
        )

    async def revoke(self, session_id: uuid.UUID, version: int) -> None:
        """Mark inactive (update, not delete) and notify realtime gateways to drop connections."""
        await self.put(session_id, version, active=False)
        await self._redis.publish(REVOCATION_CHANNEL, str(session_id))  # pyright: ignore[reportUnknownMemberType]


class SessionValidator:
    def __init__(
        self, jwt: JwtService, cache: SessionCache, sessionmaker: async_sessionmaker[AsyncSession]
    ) -> None:
        self._jwt = jwt
        self._cache = cache
        self._sessionmaker = sessionmaker

    async def authenticate(self, token: str) -> AuthContext:
        try:
            claims = self._jwt.verify(token)
        except TokenError as exc:
            raise DomainError("UNAUTHENTICATED", "Authentication required") from exc

        cached = await self._cache.get(claims.session_id)
        if cached is None:
            cached = await self._load(claims.session_id, claims.user_id)
            if cached is not None:
                await self._cache.put(claims.session_id, *cached)
        if cached is None:
            raise DomainError("UNAUTHENTICATED", "Authentication required")
        version, active = cached
        if not active or version != claims.session_version:
            raise DomainError("UNAUTHENTICATED", "Session is no longer valid")
        return AuthContext(user_id=claims.user_id, session_id=claims.session_id)

    async def _load(self, session_id: uuid.UUID, user_id: uuid.UUID) -> tuple[int, bool] | None:
        async with system_session(sessionmaker=self._sessionmaker) as s:
            row = (
                await s.execute(
                    select(m.AuthSession.session_version, m.AuthSession.invalidated_at, m.User.status)
                    .join(m.User, m.User.id == m.AuthSession.user_id)
                    .where(m.AuthSession.id == session_id, m.AuthSession.user_id == user_id)
                )
            ).one_or_none()
        if row is None:
            return None
        version, invalidated_at, status = row
        return int(version), invalidated_at is None and status == "active"
