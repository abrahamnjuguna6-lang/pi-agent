"""Promise Ledger, integrity score and accountability endpoints (design §26.2, §11, §14, §19.8)."""

from __future__ import annotations

import uuid
from datetime import date
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
from lifeos.domain.commitments import SortField
from lifeos.domain.idempotency import StoredResult

router = APIRouter(tags=["commitments"])


def _body(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_unset=True)


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: Literal["Goal", "Objective", "Project", "Task", "DailyAction"]
    entity_id: uuid.UUID


class CommitmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    due_date: date
    source: Literal["User-stated", "AI-recommended"] = "User-stated"
    completion_condition: Literal["single", "all", "any", "explicit"] | None = None
    links: list[LinkIn] = Field(default_factory=list, max_length=20)


class DeferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_due_date: date
    acknowledged: bool = False


class ExplanationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation: str = Field(min_length=1, max_length=2000)


class ReflectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    obstacle: str = Field(min_length=1, max_length=2000)
    easier: str = Field(min_length=1, max_length=2000)
    decision: Literal["keep", "change", "drop"]


# --------------------------------------------------------------------------- commitments (R9)


@router.post("/commitments", status_code=201)
async def create_commitment(
    body: CommitmentIn, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        data = body.model_dump()
        commitment = await c.commitments.create(s, user.user_id, data)
        return StoredResult(201, envelope(request, ser.commitment(commitment)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.get("/commitments")
async def list_commitments(
    request: Request,
    user: CurrentUser,
    c: ContainerDep,
    status: Literal["Open", "Kept", "Broken", "Deferred", "Cancelled"] | None = None,
    goal_category: str | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    sort: SortField = "created_at",
    order: Literal["asc", "desc"] = "desc",
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        page = await c.commitments.list_commitments(
            s,
            user.user_id,
            status=status,
            goal_category=goal_category,
            due_from=due_from,
            due_to=due_to,
            sort=sort,
            descending=order == "desc",
            cursor=cursor,
            limit=limit,
        )
        return envelope(request, [ser.commitment(x) for x in page.items], next_cursor=page.next_cursor)


@router.get("/commitments/{commitment_id}")
async def get_commitment(
    commitment_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(
            request, ser.commitment_detail(await c.commitments.detail(s, user.user_id, commitment_id))
        )


@router.post("/commitments/{commitment_id}/keep")
async def keep_commitment(
    commitment_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        commitment = await c.commitments.keep(s, user.user_id, commitment_id)
        return StoredResult(200, envelope(request, ser.commitment(commitment)))

    return await idempotent(request, c, user, key, {}, op)


@router.post("/commitments/{commitment_id}/cancel")
async def cancel_commitment(
    commitment_id: uuid.UUID, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        commitment = await c.commitments.cancel(s, user.user_id, commitment_id)
        return StoredResult(200, envelope(request, ser.commitment(commitment)))

    return await idempotent(request, c, user, key, {}, op)


@router.post("/commitments/{commitment_id}/defer")
async def defer_commitment(
    commitment_id: uuid.UUID,
    body: DeferIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        commitment = await c.commitments.defer(
            s, user.user_id, commitment_id, body.new_due_date, body.acknowledged
        )
        return StoredResult(200, envelope(request, ser.commitment(commitment)))

    return await idempotent(request, c, user, key, _body(body), op)


@router.post("/commitments/{commitment_id}/explanation", status_code=201)
async def explain_commitment(
    commitment_id: uuid.UUID,
    body: ExplanationIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    """Step 1 of a re-deferral. Step 2 is POST /defer with `acknowledged: true` and a new due date."""

    async def op(s: AsyncSession) -> StoredResult:
        commitment = await c.commitments.explain(s, user.user_id, commitment_id, body.explanation)
        data = ser.commitment(commitment) | {"acknowledgment_required": True}
        return StoredResult(201, envelope(request, data))

    return await idempotent(request, c, user, key, _body(body), op)


# --------------------------------------------------------------------------- integrity score (R9.11–9.14)


@router.get("/integrity-score")
async def integrity_score(request: Request, user: CurrentUser, c: ContainerDep) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.integrity_overview(await c.integrity.overview(s, user.user_id)))


# --------------------------------------------------------------------------- accountability (R8)


@router.get("/accountability/escalations")
async def list_escalations(
    request: Request, user: CurrentUser, c: ContainerDep, include_resolved: bool = False
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        views = await c.accountability.list_escalations(s, user.user_id, include_resolved)
        return envelope(request, [ser.escalation(v) for v in views])


@router.get("/accountability/escalations/{escalation_id}")
async def get_escalation(
    escalation_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(
            request, ser.escalation(await c.accountability.get_escalation(s, user.user_id, escalation_id))
        )


@router.post("/accountability/escalations/{escalation_id}/reflection", status_code=201)
async def submit_reflection(
    escalation_id: uuid.UUID,
    body: ReflectionIn,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        state, reflection = await c.accountability.submit_reflection(
            s, user.user_id, escalation_id, body.model_dump()
        )
        data = {
            "escalation": ser.escalation(await c.accountability.get_escalation(s, user.user_id, state.id)),
            "reflection": ser.reflection(reflection),
        }
        return StoredResult(201, envelope(request, data))

    return await idempotent(request, c, user, key, _body(body), op)
