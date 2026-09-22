"""Profile & preferences (design §26.2 "Profile and onboarding", R16.1, R16.6, R8.9, R14.3)."""

from __future__ import annotations

from datetime import time
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.api import serializers as ser
from lifeos.api.deps import ContainerDep, CurrentUser
from lifeos.api.errors import envelope
from lifeos.api.idempotency import IdempotencyKey, idempotent
from lifeos.db.session import user_session
from lifeos.domain.idempotency import StoredResult

router = APIRouter(tags=["profile"])

LifeCategory = Literal["Life", "Career", "Personal", "Spiritual", "Fitness", "Family"]


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    wake_time: time | None = None
    sleep_time: time | None = None
    working_hours_start: time | None = None
    working_hours_end: time | None = None
    accountability_style: Literal["Gentle", "Balanced", "Direct", "Strict"] | None = None
    integrity_score_threshold: int | None = Field(default=None, ge=0, le=100)
    briefing_time: time | None = None
    reflection_time: time | None = None
    ceo_meeting_time: time | None = None
    life_categories: list[LifeCategory] | None = None
    values_text: str | None = Field(default=None, max_length=5000)
    principles_text: str | None = Field(default=None, max_length=5000)
    vision_statement: str | None = Field(default=None, max_length=5000)


class PrefsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    do_not_disturb: dict[str, Any] | None = None
    types: dict[str, dict[str, bool]] | None = None


@router.get("/me")
async def get_me(user: CurrentUser, request: Request, c: ContainerDep) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.profile(await c.profile.get(s, user.user_id)))


@router.patch("/me")
async def patch_me(
    body: ProfilePatch, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    changes = body.model_dump(exclude_unset=True)

    async def op(s: AsyncSession) -> StoredResult:
        profile = await c.profile.update(s, user.user_id, changes)
        return StoredResult(200, envelope(request, ser.profile(profile)))

    return await idempotent(request, c, user, key, body.model_dump(mode="json", exclude_unset=True), op)


@router.patch("/me/notification-preferences")
async def patch_prefs(
    body: PrefsPatch, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    patch = body.model_dump(exclude_unset=True, exclude_none=True)

    async def op(s: AsyncSession) -> StoredResult:
        prefs = await c.profile.update_notification_prefs(s, user.user_id, patch)
        return StoredResult(200, envelope(request, prefs.model_dump(mode="json")))

    return await idempotent(request, c, user, key, patch, op)
