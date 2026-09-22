"""User profile and preferences (design §24.2, R16.1–16.2, R16.6)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import time
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.db import models as m
from lifeos.domain.clock import Clock
from lifeos.domain.errors import DomainError, NotFoundError
from lifeos.domain.notification_prefs import NotificationPrefs, merge_prefs, parse_prefs
from lifeos.domain.timeutil import zone
from lifeos.security.crypto import EnvelopeCipher

LifeCategory = Literal["Life", "Career", "Personal", "Spiritual", "Fitness", "Family"]
AccountabilityStyle = Literal["Gentle", "Balanced", "Direct", "Strict"]
LIFE_CATEGORIES: tuple[str, ...] = ("Life", "Career", "Personal", "Spiritual", "Fitness", "Family")

# Hook signature: (session, user_id, old_tz, new_tz). Wired to Daily Action recomputation in T5.7.
TimezoneHook = Callable[[AsyncSession, uuid.UUID, str, str], Awaitable[None]]

PLAIN_FIELDS = (
    "wake_time",
    "sleep_time",
    "working_hours_start",
    "working_hours_end",
    "accountability_style",
    "integrity_score_threshold",
    "briefing_time",
    "reflection_time",
    "ceo_meeting_time",
    "life_categories",
    "values_text",
    "principles_text",
    "vision_statement",
)


@dataclass(frozen=True)
class Profile:
    id: uuid.UUID
    email: str
    email_verified: bool
    full_name: str | None
    timezone: str
    wake_time: time | None
    sleep_time: time | None
    working_hours_start: time | None
    working_hours_end: time | None
    accountability_style: str
    integrity_score_threshold: int
    briefing_time: time
    reflection_time: time
    ceo_meeting_time: time
    life_categories: list[str]
    values_text: str | None
    principles_text: str | None
    vision_statement: str | None
    onboarding_completed: bool
    notification_prefs: NotificationPrefs


class ProfileService:
    def __init__(
        self, cipher: EnvelopeCipher, clock: Clock, timezone_hooks: list[TimezoneHook] | None = None
    ) -> None:
        self._cipher = cipher
        self._clock = clock
        self._tz_hooks = list(timezone_hooks or [])

    def on_timezone_change(self, hook: TimezoneHook) -> None:
        self._tz_hooks.append(hook)

    async def _user(self, s: AsyncSession, user_id: uuid.UUID) -> m.User:
        user = (await s.execute(select(m.User).where(m.User.id == user_id))).scalar_one_or_none()
        if user is None:
            raise NotFoundError("user")
        return user

    def _to_profile(self, u: m.User) -> Profile:
        return Profile(
            id=u.id,
            email=self._cipher.decrypt_str(u.email_ciphertext, associated_data=b"users.email"),
            email_verified=u.email_verified,
            full_name=(
                self._cipher.decrypt_str(u.full_name_ciphertext, associated_data=b"users.full_name")
                if u.full_name_ciphertext
                else None
            ),
            timezone=u.timezone,
            wake_time=u.wake_time,
            sleep_time=u.sleep_time,
            working_hours_start=u.working_hours_start,
            working_hours_end=u.working_hours_end,
            accountability_style=u.accountability_style,
            integrity_score_threshold=u.integrity_score_threshold,
            briefing_time=u.briefing_time,
            reflection_time=u.reflection_time,
            ceo_meeting_time=u.ceo_meeting_time,
            life_categories=list(u.life_categories),
            values_text=u.values_text,
            principles_text=u.principles_text,
            vision_statement=u.vision_statement,
            onboarding_completed=u.onboarding_completed,
            notification_prefs=parse_prefs(u.notification_prefs),
        )

    async def get(self, s: AsyncSession, user_id: uuid.UUID) -> Profile:
        return self._to_profile(await self._user(s, user_id))

    async def update(self, s: AsyncSession, user_id: uuid.UUID, changes: dict[str, Any]) -> Profile:
        """Apply a partial update (R16.6). Keys absent from `changes` are left untouched."""
        user = await self._user(s, user_id)
        _validate(changes)
        old_tz = user.timezone
        if "timezone" in changes:
            user.timezone = zone(changes["timezone"]).key
        if "full_name" in changes:
            name = changes["full_name"]
            user.full_name_ciphertext = (
                self._cipher.encrypt_str(name, associated_data=b"users.full_name") if name else None
            )
        for field in PLAIN_FIELDS:
            if field in changes:
                setattr(user, field, changes[field])
        user.updated_at = self._clock.now()
        await s.flush()
        if user.timezone != old_tz:
            for hook in self._tz_hooks:
                await hook(s, user_id, old_tz, user.timezone)
        return self._to_profile(user)

    async def update_notification_prefs(
        self, s: AsyncSession, user_id: uuid.UUID, patch: dict[str, Any]
    ) -> NotificationPrefs:
        user = await self._user(s, user_id)
        prefs = merge_prefs(user.notification_prefs, patch)
        user.notification_prefs = prefs.model_dump(mode="json")
        user.updated_at = self._clock.now()
        await s.flush()
        return prefs


def _validate(changes: dict[str, Any]) -> None:
    categories = changes.get("life_categories")
    if categories is not None:
        unknown = set(categories) - set(LIFE_CATEGORIES)
        if unknown:
            raise DomainError(
                "VALIDATION_ERROR",
                f"Unknown life categories: {sorted(unknown)}",
                {"field": "life_categories"},
            )
        if len(set(categories)) != len(categories):
            raise DomainError("VALIDATION_ERROR", "Duplicate life categories", {"field": "life_categories"})
    for field in (
        "timezone",
        "accountability_style",
        "integrity_score_threshold",
        "briefing_time",
        "reflection_time",
        "ceo_meeting_time",
    ):
        if field in changes and changes[field] is None:
            raise DomainError("VALIDATION_ERROR", f"{field} cannot be cleared", {"field": field})
