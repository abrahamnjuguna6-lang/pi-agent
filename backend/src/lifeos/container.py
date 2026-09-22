"""Composition root: builds shared infrastructure and services once per process.

The API lifespan and the worker call `build_container(settings)`; tests build one with test
doubles (frozen clock, in-memory email) against the test database and Redis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.config import Settings
from lifeos.db.engine import get_sessionmaker
from lifeos.domain.auth.service import AuthService
from lifeos.domain.auth.sessions import SessionCache, SessionValidator
from lifeos.domain.clock import Clock, SystemClock
from lifeos.notifications.email import EmailSender, LoggingEmailSender
from lifeos.security.crypto import EnvelopeCipher, LocalKeyProvider
from lifeos.security.hashing import KeyedHasher, PasswordHasher
from lifeos.security.jwt import JwtService, load_signing_key


@dataclass
class Container:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    clock: Clock
    cipher: EnvelopeCipher
    hasher: KeyedHasher
    passwords: PasswordHasher
    jwt: JwtService
    email: EmailSender
    session_cache: SessionCache
    session_validator: SessionValidator
    auth: AuthService

    async def aclose(self) -> None:
        await self.redis.aclose()


def build_container(
    settings: Settings,
    *,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    redis: Redis | None = None,
    clock: Clock | None = None,
    email: EmailSender | None = None,
) -> Container:
    dev = settings.environment in ("development", "test")
    maker = sessionmaker or get_sessionmaker()
    redis_client = redis or Redis.from_url(str(settings.redis_url))
    clk = clock or SystemClock()

    key_secrets = {settings.encryption_key_id: settings.encryption_key.get_secret_value().encode()}
    key_secrets |= {k: v.get_secret_value().encode() for k, v in settings.encryption_retired_keys.items()}
    cipher = EnvelopeCipher(LocalKeyProvider(key_secrets, settings.encryption_key_id))
    hasher = KeyedHasher(settings.hmac_key.get_secret_value().encode())
    passwords = PasswordHasher(
        settings.argon2_memory_kib, settings.argon2_time_cost, settings.argon2_parallelism
    )
    signing = load_signing_key(
        settings.jwt_key_id, settings.jwt_private_key.get_secret_value(), allow_ephemeral=dev
    )
    jwt = JwtService(signing, clk, timedelta(minutes=settings.access_token_ttl_minutes))
    cache = SessionCache(redis_client, ttl_seconds=jwt.ttl_seconds + 60)
    mail = email or LoggingEmailSender()

    return Container(
        settings=settings,
        sessionmaker=maker,
        redis=redis_client,
        clock=clk,
        cipher=cipher,
        hasher=hasher,
        passwords=passwords,
        jwt=jwt,
        email=mail,
        session_cache=cache,
        session_validator=SessionValidator(jwt, cache, maker),
        auth=AuthService(
            settings=settings,
            sessionmaker=maker,
            redis=redis_client,
            clock=clk,
            passwords=passwords,
            hasher=hasher,
            cipher=cipher,
            jwt=jwt,
            email=mail,
            session_cache=cache,
        ),
    )
