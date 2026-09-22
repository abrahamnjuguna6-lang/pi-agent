"""T2.3: registration, email verification, login, lockout (R17.1–17.3, R17.8)."""

from datetime import timedelta

import httpx
from sqlalchemy import select

from lifeos.db import models as m
from lifeos.db.session import system_session
from tests.integration.api_harness import Api

EMAIL = "alice@example.com"


def error_code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


async def test_register_verify_login_flow(api: Api) -> None:
    r = await api.register(EMAIL)
    assert r.status_code == 201
    assert r.json()["data"]["email_verification_required"] is True

    # R17.3: login before verification is rejected and prompts verification.
    r = await api.login(EMAIL)
    assert r.status_code == 403
    assert error_code(r) == "EMAIL_NOT_VERIFIED"

    token = api.link_token("verify_email", EMAIL)
    r = await api.client.post("/api/v1/auth/email-verification/confirm", json={"token": token})
    assert r.status_code == 200

    tokens = await api.tokens(EMAIL)
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 3600
    me = await api.me(tokens["access_token"])
    assert me.status_code == 200
    assert me.json()["data"]["email"] == EMAIL
    assert me.json()["data"]["email_verified"] is True


async def test_email_is_encrypted_and_lookup_is_hashed(api: Api) -> None:
    await api.register(EMAIL)
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        user = (await s.execute(select(m.User))).scalar_one()
    assert EMAIL.encode() not in user.email_ciphertext
    assert user.email_lookup_hash == api.container.hasher.email_lookup_hash(EMAIL)
    assert user.password_hash.startswith("$argon2id$")


async def test_duplicate_email_rejected_case_insensitive(api: Api) -> None:
    assert (await api.register(EMAIL)).status_code == 201
    r = await api.register("  ALICE@Example.com ")
    assert r.status_code == 409
    assert error_code(r) == "EMAIL_TAKEN"


async def test_weak_password_and_invalid_email_rejected(api: Api) -> None:
    r = await api.register(EMAIL, "short")
    assert (r.status_code, error_code(r)) == (422, "WEAK_PASSWORD")
    r = await api.register("not-an-email")
    assert (r.status_code, error_code(r)) == (422, "VALIDATION_ERROR")


async def test_unknown_email_and_wrong_password_look_identical(api: Api) -> None:
    await api.verified_user(EMAIL)
    unknown = await api.login("nobody@example.com")
    wrong = await api.login(EMAIL, "wrong password!!")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"] == wrong.json()["error"]


async def test_verification_token_single_use_and_expiring(api: Api) -> None:
    await api.register(EMAIL)
    token = api.link_token("verify_email", EMAIL)
    api.clock.set(api.clock.now() + timedelta(hours=24))
    r = await api.client.post("/api/v1/auth/email-verification/confirm", json={"token": token})
    assert (r.status_code, error_code(r)) == (401, "INVALID_TOKEN")

    await api.client.post("/api/v1/auth/email-verification/resend", json={"email": EMAIL})
    fresh = api.link_token("verify_email", EMAIL)
    assert fresh != token
    assert (
        await api.client.post("/api/v1/auth/email-verification/confirm", json={"token": fresh})
    ).status_code == 200
    reuse = await api.client.post("/api/v1/auth/email-verification/confirm", json={"token": fresh})
    assert reuse.status_code == 401


async def test_resend_for_unknown_email_is_silent(api: Api) -> None:
    r = await api.client.post("/api/v1/auth/email-verification/resend", json={"email": "ghost@example.com"})
    assert r.status_code == 202
    assert api.email.outbox == []


async def test_lockout_after_five_failures_then_unlock(api: Api) -> None:
    await api.verified_user(EMAIL)
    for attempt in range(5):
        r = await api.login(EMAIL, "wrong password!!")
        assert (r.status_code, error_code(r)) == (401, "INVALID_CREDENTIALS"), attempt

    # R17.8: the account is locked; even the correct password is refused.
    r = await api.login(EMAIL)
    assert (r.status_code, error_code(r)) == (423, "ACCOUNT_LOCKED")
    assert api.email.last("account_locked", EMAIL)

    api.clock.set(api.clock.now() + timedelta(minutes=14, seconds=59))
    assert (await api.login(EMAIL)).status_code == 423
    api.clock.set(api.clock.now() + timedelta(seconds=1))
    assert (await api.login(EMAIL)).status_code == 200


async def test_failures_outside_window_do_not_lock(api: Api) -> None:
    await api.verified_user(EMAIL)
    for _ in range(4):
        await api.login(EMAIL, "wrong password!!")
    api.clock.set(api.clock.now() + timedelta(minutes=11))  # earlier failures leave the 10-minute window
    for _ in range(4):
        assert (await api.login(EMAIL, "wrong password!!")).status_code == 401
    assert (await api.login(EMAIL)).status_code == 200


async def test_successful_login_resets_failure_count(api: Api) -> None:
    await api.verified_user(EMAIL)
    for _ in range(4):
        await api.login(EMAIL, "wrong password!!")
    assert (await api.login(EMAIL)).status_code == 200
    for _ in range(4):
        assert (await api.login(EMAIL, "wrong password!!")).status_code == 401
    assert (await api.login(EMAIL)).status_code == 200


async def test_ip_throttling(api: Api) -> None:
    for i in range(20):
        await api.login(f"user{i}@example.com", "wrong password!!")
    r = await api.login("another@example.com", "whatever password")
    assert (r.status_code, error_code(r)) == (429, "RATE_LIMITED")
