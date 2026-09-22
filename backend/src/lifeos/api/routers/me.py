"""Current-user profile. Minimal read in M2; full profile & preferences arrive in T4.1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from lifeos.api.deps import ContainerDep, CurrentUser
from lifeos.api.errors import envelope
from lifeos.db import models as m
from lifeos.db.session import user_session
from lifeos.domain.errors import NotFoundError

router = APIRouter(tags=["profile"])


@router.get("/me")
async def get_me(user: CurrentUser, request: Request, c: ContainerDep) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        row = (await s.execute(select(m.User).where(m.User.id == user.user_id))).scalar_one_or_none()
        if row is None:
            raise NotFoundError("user")
        data = {
            "id": str(row.id),
            "email": c.cipher.decrypt_str(row.email_ciphertext, associated_data=b"users.email"),
            "email_verified": row.email_verified,
            "timezone": row.timezone,
            "accountability_style": row.accountability_style,
            "onboarding_completed": row.onboarding_completed,
        }
    return envelope(request, data)
