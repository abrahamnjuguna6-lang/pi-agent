"""In-process API harness: real app + test DB + test Redis, frozen clock, captured email."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lifeos.config import Settings
from lifeos.container import Container, build_container
from lifeos.domain.clock import FrozenClock
from lifeos.main import create_app
from lifeos.notifications.email import InMemoryEmailSender
from tests.conftest import TEST_ENV

T0 = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
PASSWORD = "correct horse battery"


def test_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {k.lower(): v for k, v in TEST_ENV.items()}
    values.update(argon2_memory_kib=19456, argon2_time_cost=2)  # fast but valid Argon2id
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@dataclass
class Api:
    client: httpx.AsyncClient
    container: Container
    clock: FrozenClock
    email: InMemoryEmailSender

    async def register(self, email: str, password: str = PASSWORD) -> httpx.Response:
        return await self.client.post("/api/v1/auth/register", json={"email": email, "password": password})

    def link_token(self, kind: str, to: str) -> str:
        link = self.email.last(kind, to).link  # type: ignore[arg-type]
        assert link is not None
        return parse_qs(urlparse(link).query)["token"][0]

    async def verified_user(self, email: str, password: str = PASSWORD) -> None:
        assert (await self.register(email, password)).status_code == 201
        token = self.link_token("verify_email", email)
        r = await self.client.post("/api/v1/auth/email-verification/confirm", json={"token": token})
        assert r.status_code == 200, r.text

    async def login(self, email: str, password: str = PASSWORD) -> httpx.Response:
        return await self.client.post("/api/v1/auth/login", json={"email": email, "password": password})

    async def tokens(self, email: str, password: str = PASSWORD) -> dict[str, Any]:
        r = await self.login(email, password)
        assert r.status_code == 200, r.text
        return r.json()["data"]

    async def me(self, access_token: str) -> httpx.Response:
        return await self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {access_token}"})

    async def as_user(self, email: str) -> AuthedClient:
        """Register + verify + log in; returns a client that authenticates every call."""
        await self.verified_user(email)
        tokens = await self.tokens(email)
        return AuthedClient(self.client, tokens["access_token"])


@dataclass
class AuthedClient:
    client: httpx.AsyncClient
    access_token: str

    def _headers(self, key: str | None, mutating: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if mutating:
            headers["Idempotency-Key"] = key or f"test-{uuid.uuid4()}"
        return headers

    async def get(self, path: str, **params: Any) -> httpx.Response:
        return await self.client.get(
            f"/api/v1{path}", params=params or None, headers=self._headers(None, False)
        )

    async def post(
        self, path: str, json: Any = None, key: str | None = None, **params: Any
    ) -> httpx.Response:
        return await self.client.post(
            f"/api/v1{path}", json=json, params=params or None, headers=self._headers(key, True)
        )

    async def patch(self, path: str, json: Any, key: str | None = None) -> httpx.Response:
        return await self.client.patch(f"/api/v1{path}", json=json, headers=self._headers(key, True))

    async def delete(self, path: str, key: str | None = None, **params: Any) -> httpx.Response:
        return await self.client.delete(
            f"/api/v1{path}", params=params or None, headers=self._headers(key, True)
        )


@pytest.fixture
async def api(db: async_sessionmaker[AsyncSession], redis_url: str) -> AsyncIterator[Api]:
    redis = Redis.from_url(redis_url)
    await redis.flushdb()
    clock = FrozenClock(T0)
    email = InMemoryEmailSender()
    container = build_container(test_settings(), sessionmaker=db, redis=redis, clock=clock, email=email)
    app = create_app(container)
    app.state.container = container  # ASGITransport does not run the lifespan
    transport = httpx.ASGITransport(app=app, client=("203.0.113.7", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Api(client=client, container=container, clock=clock, email=email)
    await redis.flushdb()
    await redis.aclose()
