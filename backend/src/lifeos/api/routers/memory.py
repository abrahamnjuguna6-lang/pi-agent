"""Memory Store endpoints: browse, add, edit, delete, semantic search (design §26.2, §23)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.api import serializers as ser
from lifeos.api.deps import ContainerDep, CurrentUser
from lifeos.api.errors import envelope
from lifeos.api.idempotency import IdempotencyKey, idempotent
from lifeos.db.session import user_session
from lifeos.domain.errors import DomainError
from lifeos.domain.idempotency import StoredResult
from lifeos.domain.memory.service import MemoryDraft

router = APIRouter(tags=["memory"])

CONFIRM_PHRASE = "DELETE"  # X-Confirm-Phrase for the destructive memory delete (design §23.6)
# What the User may add directly (design §10.7 "User adds via UI"); everything else is agent-created.
USER_TYPES = ("Value", "Principle", "Fact", "Preference")


class MemoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    type: Literal["Value", "Principle", "Fact", "Preference"]
    categories: list[str] = Field(default_factory=list, max_length=14)
    importance: int | None = Field(default=None, ge=1, le=10)


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    importance: int | None = Field(default=None, ge=1, le=10)


@router.post("/memory", status_code=201)
async def add_memory(
    body: MemoryIn, request: Request, user: CurrentUser, key: IdempotencyKey, c: ContainerDep
) -> Response:
    """R4.6: the User adds a Value, Principle, Fact or Preference themselves; the request is the
    confirmation (design §10.7)."""

    async def op(s: AsyncSession) -> StoredResult:
        entry = await c.memory.create(
            s,
            user.user_id,
            MemoryDraft(
                content=body.content,
                type=body.type,
                source="User-stated",
                categories=body.categories,
                importance=body.importance,
            ),
            "user_request",
        )
        return StoredResult(201, envelope(request, ser.memory_entry(entry)))

    return await idempotent(request, c, user, key, body.model_dump(mode="json", exclude_unset=True), op)


@router.get("/memory")
async def browse_memory(
    request: Request,
    user: CurrentUser,
    c: ContainerDep,
    type: str | None = None,
    source: Literal["User-stated", "AI-inferred", "System-derived"] | None = None,
    category: str | None = None,
    q: str | None = None,
    created_from: date | None = None,
    include_superseded: bool = False,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        page = await c.memory.browse(
            s,
            user.user_id,
            type=type,
            source=source,
            category=category,
            q=q,
            created_from=created_from,
            include_superseded=include_superseded,
            cursor=cursor,
            limit=limit,
        )
        return envelope(request, [ser.memory_entry(e) for e in page.items], next_cursor=page.next_cursor)


@router.get("/memory/search")
async def search_memory(
    request: Request,
    user: CurrentUser,
    c: ContainerDep,
    q: str,
    k: int | None = None,
    threshold: float | None = None,
    type: str | None = None,
) -> dict[str, Any]:
    """R4.5/R4.9: top-k by cosine similarity, descending (R15.10)."""
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        hits = await c.memory_search.search(
            s, user.user_id, q, k=k, threshold=threshold, types=[type] if type else None
        )
        return envelope(request, [ser.memory_hit(hit) for hit in hits])


@router.get("/memory/{entry_id}")
async def get_memory(
    entry_id: uuid.UUID, request: Request, user: CurrentUser, c: ContainerDep
) -> dict[str, Any]:
    async with user_session(user.user_id, sessionmaker=c.sessionmaker) as s:
        return envelope(request, ser.memory_entry(await c.memory.get(s, user.user_id, entry_id)))


@router.patch("/memory/{entry_id}")
async def patch_memory(
    entry_id: uuid.UUID,
    body: MemoryPatch,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
) -> Response:
    async def op(s: AsyncSession) -> StoredResult:
        entry = await c.memory.update(s, user.user_id, entry_id, body.model_dump(exclude_unset=True))
        return StoredResult(200, envelope(request, ser.memory_entry(entry)))

    return await idempotent(request, c, user, key, body.model_dump(mode="json", exclude_unset=True), op)


@router.delete("/memory/{entry_id}", status_code=204)
async def delete_memory(
    entry_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    key: IdempotencyKey,
    c: ContainerDep,
    x_confirm_phrase: Annotated[str | None, Header()] = None,
) -> Response:
    """R18.8: an individual entry is deleted only after strong confirmation — the client must echo the
    phrase it showed the User."""
    if x_confirm_phrase != CONFIRM_PHRASE:
        raise DomainError(
            "CONFIRMATION_REQUIRED",
            f"Send the X-Confirm-Phrase header with {CONFIRM_PHRASE} to delete this entry",
            {"phrase": CONFIRM_PHRASE},
        )

    async def op(s: AsyncSession) -> StoredResult:
        await c.memory.delete(s, user.user_id, entry_id)
        return StoredResult(204, None)

    return await idempotent(request, c, user, key, {}, op)
