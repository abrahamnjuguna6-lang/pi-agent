"""Authentication endpoints (design §26.2 "Auth", R17)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from lifeos.api.deps import ContainerDep, CurrentUser, client_ip
from lifeos.api.errors import envelope
from lifeos.domain.auth.service import TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    full_name: str | None = Field(default=None, max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class RefreshIn(BaseModel):
    refresh_token: str = Field(max_length=512)


class TokenIn(BaseModel):
    token: str = Field(max_length=512)


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


class ResetConfirmIn(BaseModel):
    token: str = Field(max_length=512)
    new_password: str = Field(max_length=256)


class TokenPairOut(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime

    @classmethod
    def of(cls, pair: TokenPair) -> TokenPairOut:
        return cls(
            access_token=pair.access_token,
            token_type=pair.token_type,
            expires_in=pair.expires_in,
            refresh_token=pair.refresh_token,
            refresh_expires_at=pair.refresh_expires_at,
        )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, request: Request, c: ContainerDep) -> dict[str, Any]:
    user_id = await c.auth.register(body.email, body.password, body.full_name)
    return envelope(request, {"user_id": str(user_id), "email_verification_required": True})


@router.post("/login")
async def login(body: LoginIn, request: Request, c: ContainerDep) -> dict[str, Any]:
    pair = await c.auth.login(body.email, body.password, client_ip(request))
    return envelope(request, TokenPairOut.of(pair).model_dump(mode="json"))


@router.post("/refresh")
async def refresh(body: RefreshIn, request: Request, c: ContainerDep) -> dict[str, Any]:
    pair = await c.auth.refresh(body.refresh_token)
    return envelope(request, TokenPairOut.of(pair).model_dump(mode="json"))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CurrentUser, c: ContainerDep) -> None:
    await c.auth.logout(user)


@router.post("/email-verification/confirm")
async def confirm_email(body: TokenIn, request: Request, c: ContainerDep) -> dict[str, Any]:
    await c.auth.verify_email(body.token)
    return envelope(request, {"email_verified": True})


@router.post("/email-verification/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(body: EmailIn, request: Request, c: ContainerDep) -> dict[str, Any]:
    await c.auth.resend_verification(body.email)
    return envelope(request, {"accepted": True})


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_reset(body: EmailIn, request: Request, c: ContainerDep) -> dict[str, Any]:
    await c.auth.request_password_reset(body.email)
    return envelope(request, {"accepted": True})


@router.post("/password-reset/confirm")
async def confirm_reset(body: ResetConfirmIn, request: Request, c: ContainerDep) -> dict[str, Any]:
    await c.auth.confirm_password_reset(body.token, body.new_password)
    return envelope(request, {"password_changed": True})
