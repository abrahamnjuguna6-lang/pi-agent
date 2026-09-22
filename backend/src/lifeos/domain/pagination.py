"""Keyset (cursor) pagination on (created_at, id) — stable under concurrent inserts (design §26.1)."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

from lifeos.domain.errors import DomainError

T = TypeVar("T")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps({"c": created_at.isoformat(), "i": str(row_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(data["c"]), uuid.UUID(data["i"])
    except (ValueError, KeyError, TypeError) as exc:
        raise DomainError("VALIDATION_ERROR", "Invalid cursor", {"field": "cursor"}) from exc


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if not 1 <= limit <= MAX_LIMIT:
        raise DomainError("VALIDATION_ERROR", f"limit must be between 1 and {MAX_LIMIT}", {"field": "limit"})
    return limit


def keyset(
    query: Select[Any],
    created_at: InstrumentedAttribute[datetime],
    row_id: InstrumentedAttribute[uuid.UUID],
    cursor: str | None,
    limit: int,
) -> Select[Any]:
    """Ascending (created_at, id) order; fetch limit+1 to detect a next page."""
    if cursor:
        c_at, c_id = decode_cursor(cursor)
        query = query.where(or_(created_at > c_at, and_(created_at == c_at, row_id > c_id)))
    return query.order_by(created_at, row_id).limit(limit + 1)


def to_page(rows: list[Any], limit: int) -> Page[Any]:
    if len(rows) <= limit:
        return Page(rows, None)
    last = rows[limit - 1]
    return Page(rows[:limit], encode_cursor(last.created_at, last.id))
