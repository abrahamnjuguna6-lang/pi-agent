"""T2.4: refresh rotation + replay detection, logout invalidation, password reset (R17.4–17.6)."""

from datetime import timedelta

import httpx

from tests.integration.api_harness import Api

EMAIL = "carol@example.com"


async def refresh(api: Api, token: str) -> httpx.Response:
    return await api.client.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def test_refresh_rotates_and_old_token_fails(api: Api) -> None:
    await api.verified_user(EMAIL)
    first = await api.tokens(EMAIL)
    r = await refresh(api, first["refresh_token"])
    assert r.status_code == 200
    second = r.json()["data"]
    assert second["refresh_token"] != first["refresh_token"]
    assert (await api.me(second["access_token"])).status_code == 200
    assert (await refresh(api, first["refresh_token"])).status_code == 401


async def test_replayed_refresh_token_kills_the_family(api: Api) -> None:
    await api.verified_user(EMAIL)
    first = await api.tokens(EMAIL)
    second = (await refresh(api, first["refresh_token"])).json()["data"]

    # Attacker replays the retired token → whole family invalidated.
    assert (await refresh(api, first["refresh_token"])).status_code == 401
    assert (await refresh(api, second["refresh_token"])).status_code == 401
    assert (await api.me(second["access_token"])).status_code == 401


async def test_refresh_expires_after_24_hours(api: Api) -> None:
    await api.verified_user(EMAIL)
    tokens = await api.tokens(EMAIL)
    api.clock.set(api.clock.now() + timedelta(hours=24))
    assert (await refresh(api, tokens["refresh_token"])).status_code == 401


async def test_logout_invalidates_access_and_refresh_immediately(api: Api) -> None:
    await api.verified_user(EMAIL)
    tokens = await api.tokens(EMAIL)
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await api.client.post("/api/v1/auth/logout", headers=auth)).status_code == 204
    assert (await api.me(tokens["access_token"])).status_code == 401  # well before the 1 h expiry
    assert (await refresh(api, tokens["refresh_token"])).status_code == 401


async def test_logout_only_affects_that_session(api: Api) -> None:
    await api.verified_user(EMAIL)
    phone, laptop = await api.tokens(EMAIL), await api.tokens(EMAIL)
    await api.client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {phone['access_token']}"})
    assert (await api.me(phone["access_token"])).status_code == 401
    assert (await api.me(laptop["access_token"])).status_code == 200


async def test_logout_publishes_revocation(api: Api) -> None:
    await api.verified_user(EMAIL)
    tokens = await api.tokens(EMAIL)
    sid = str(api.container.jwt.verify(tokens["access_token"]).session_id)
    pubsub = api.container.redis.pubsub()
    await pubsub.subscribe("auth:revoked")
    await pubsub.get_message(timeout=1)  # subscription confirmation
    await api.client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    await pubsub.aclose()
    assert message is not None
    assert message["data"].decode() == sid


async def test_password_reset_invalidates_all_sessions(api: Api) -> None:
    await api.verified_user(EMAIL)
    a, b = await api.tokens(EMAIL), await api.tokens(EMAIL)
    assert (
        await api.client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
    ).status_code == 202
    token = api.link_token("password_reset", EMAIL)
    r = await api.client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "a brand new passphrase"}
    )
    assert r.status_code == 200
    for tokens in (a, b):
        assert (await api.me(tokens["access_token"])).status_code == 401
        assert (await refresh(api, tokens["refresh_token"])).status_code == 401
    assert (await api.login(EMAIL)).status_code == 401  # old password
    assert (await api.login(EMAIL, "a brand new passphrase")).status_code == 200
    assert api.email.last("password_changed", EMAIL)


async def test_reset_link_expires_after_one_hour_and_is_single_use(api: Api) -> None:
    await api.verified_user(EMAIL)
    await api.client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
    token = api.link_token("password_reset", EMAIL)
    api.clock.set(api.clock.now() + timedelta(minutes=61))
    r = await api.client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "x" * 12}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (401, "INVALID_TOKEN")

    await api.client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
    fresh = api.link_token("password_reset", EMAIL)
    body = {"token": fresh, "new_password": "another passphrase"}
    assert (await api.client.post("/api/v1/auth/password-reset/confirm", json=body)).status_code == 200
    assert (await api.client.post("/api/v1/auth/password-reset/confirm", json=body)).status_code == 401


async def test_reset_request_identical_for_unknown_email(api: Api) -> None:
    await api.verified_user(EMAIL)
    known = await api.client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
    unknown = await api.client.post(
        "/api/v1/auth/password-reset/request", json={"email": "ghost@example.com"}
    )
    assert known.status_code == unknown.status_code == 202
    assert known.json()["data"] == unknown.json()["data"]
    assert [msg.to for msg in api.email.outbox if msg.kind == "password_reset"] == [EMAIL]


async def test_reset_clears_lockout(api: Api) -> None:
    await api.verified_user(EMAIL)
    for _ in range(5):
        await api.login(EMAIL, "wrong password!!")
    assert (await api.login(EMAIL)).status_code == 423
    await api.client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
    token = api.link_token("password_reset", EMAIL)
    await api.client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "fresh passphrase"}
    )
    assert (await api.login(EMAIL, "fresh passphrase")).status_code == 200
