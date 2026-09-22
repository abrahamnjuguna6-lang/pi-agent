"""T2.2: every request validates (sid, ver); PostgreSQL is authoritative over Redis (R17.5)."""

import uuid

from sqlalchemy import update

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests.integration.api_harness import Api

EMAIL = "bob@example.com"


async def test_missing_or_malformed_bearer_rejected(api: Api) -> None:
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer not-a-jwt"}):
        r = await api.client.get("/api/v1/me", headers=headers)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"
        assert r.headers["www-authenticate"] == "Bearer"


async def test_stale_session_version_rejected(api: Api) -> None:
    await api.verified_user(EMAIL)
    access = (await api.tokens(EMAIL))["access_token"]
    assert (await api.me(access)).status_code == 200

    sid = api.container.jwt.verify(access).session_id
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        await s.execute(update(m.AuthSession).where(m.AuthSession.id == sid).values(session_version=2))
    await api.container.session_cache.put(sid, 2, active=True)
    assert (await api.me(access)).status_code == 401


async def test_database_is_authoritative_when_redis_is_empty(api: Api) -> None:
    await api.verified_user(EMAIL)
    access = (await api.tokens(EMAIL))["access_token"]
    sid = api.container.jwt.verify(access).session_id

    # Valid token survives a cache flush (repopulated from PostgreSQL).
    await api.container.redis.flushdb()
    assert (await api.me(access)).status_code == 200

    # Revoke in PostgreSQL only, then flush Redis: still rejected.
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        await s.execute(
            update(m.AuthSession)
            .where(m.AuthSession.id == sid)
            .values(session_version=m.AuthSession.session_version + 1)
        )
    await api.container.redis.flushdb()
    assert (await api.me(access)).status_code == 401


async def test_token_for_unknown_session_rejected(api: Api) -> None:
    await api.verified_user(EMAIL)
    access = (await api.tokens(EMAIL))["access_token"]
    user_id = api.container.jwt.verify(access).user_id
    forged = api.container.jwt.issue(user_id, uuid.uuid4(), 1)  # correctly signed, no such session
    assert (await api.me(forged)).status_code == 401


async def test_deletion_pending_user_rejected(api: Api) -> None:
    await api.verified_user(EMAIL)
    access = (await api.tokens(EMAIL))["access_token"]
    user_id = api.container.jwt.verify(access).user_id
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        await s.execute(update(m.User).where(m.User.id == user_id).values(status="deletion_pending"))
    await api.container.redis.flushdb()
    assert (await api.me(access)).status_code == 401
