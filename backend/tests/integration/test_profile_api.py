"""T4.1: profile & notification preferences API (R16.1–16.2, R16.6, R8.9, R14.3)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.api_harness import Api


async def test_get_me_returns_full_profile_with_default_prefs(api: Api) -> None:
    me = await api.as_user("erin@example.com")
    body = (await me.get("/me")).json()["data"]
    assert body["email"] == "erin@example.com"
    assert body["timezone"] == "UTC"
    assert body["accountability_style"] == "Balanced"
    assert (body["briefing_time"], body["reflection_time"], body["ceo_meeting_time"]) == (
        "05:00:00",
        "21:00:00",
        "19:00:00",
    )
    assert body["notification_prefs"]["types"]["accountability_l4"] == {"push": True, "in_app": True}


async def test_patch_persists_every_kind_of_field(api: Api) -> None:
    me = await api.as_user("erin@example.com")
    patch = {
        "full_name": "Erin Example",
        "timezone": "Africa/Nairobi",
        "wake_time": "06:15:00",
        "working_hours_start": "09:00:00",
        "accountability_style": "Direct",
        "integrity_score_threshold": 80,
        "briefing_time": "05:30:00",
        "life_categories": ["Career", "Fitness"],
        "values_text": "Honesty, craft",
    }
    r = await me.patch("/me", patch)
    assert r.status_code == 200, r.text
    body = (await me.get("/me")).json()["data"]
    for key, value in patch.items():
        assert body[key] == value, key


async def test_full_name_is_encrypted_at_rest(api: Api) -> None:
    from sqlalchemy import select

    from lifeos.db import models as m
    from lifeos.db.session import system_session

    me = await api.as_user("erin@example.com")
    await me.patch("/me", {"full_name": "Erin Secret"})
    async with system_session(sessionmaker=api.container.sessionmaker) as s:
        blob = (await s.execute(select(m.User.full_name_ciphertext))).scalar_one()
    assert blob is not None
    assert b"Erin Secret" not in blob


async def test_invalid_timezone_and_category_rejected(api: Api) -> None:
    me = await api.as_user("erin@example.com")
    r = await me.patch("/me", {"timezone": "Mars/Base"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "VALIDATION_ERROR")
    r = await me.patch("/me", {"life_categories": ["Hobbies"]})
    assert r.status_code == 422
    r = await me.patch("/me", {"timezone": None})
    assert r.status_code == 422
    assert (await me.get("/me")).json()["data"]["timezone"] == "UTC"  # unchanged


async def test_unknown_fields_rejected(api: Api) -> None:
    me = await api.as_user("erin@example.com")
    r = await me.patch("/me", {"email_verified": False})
    assert r.status_code == 422


async def test_timezone_change_invokes_hook(api: Api) -> None:
    calls: list[tuple[str, str]] = []

    async def hook(s: AsyncSession, user_id: uuid.UUID, old: str, new: str) -> None:
        calls.append((old, new))

    api.container.profile.on_timezone_change(hook)
    me = await api.as_user("erin@example.com")
    await me.patch("/me", {"timezone": "America/New_York"})
    await me.patch("/me", {"timezone": "America/New_York"})  # same value → no hook
    assert calls == [("UTC", "America/New_York")]


async def test_notification_preferences_patch(api: Api) -> None:
    me = await api.as_user("erin@example.com")
    r = await me.patch(
        "/me/notification-preferences",
        {"types": {"accountability_l1": {"push": False}}, "do_not_disturb": {"start_time": "23:00:00"}},
    )
    assert r.status_code == 200, r.text
    prefs = (await me.get("/me")).json()["data"]["notification_prefs"]
    assert prefs["types"]["accountability_l1"] == {"push": False, "in_app": True}
    assert prefs["types"]["accountability_l2"] == {"push": True, "in_app": True}
    assert prefs["do_not_disturb"]["start_time"] == "23:00:00"
    assert prefs["do_not_disturb"]["end_time"] == "07:00:00"


async def test_cannot_silence_level_5(api: Api) -> None:
    me = await api.as_user("erin@example.com")
    r = await me.patch("/me/notification-preferences", {"types": {"accountability_l5": {"in_app": False}}})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "VALIDATION_ERROR")


async def test_profile_mutations_require_idempotency_key(api: Api) -> None:
    me = await api.as_user("erin@example.com")
    r = await api.client.patch(
        "/api/v1/me", json={"values_text": "x"}, headers={"Authorization": f"Bearer {me.access_token}"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["details"]["header"] == "Idempotency-Key"
