"""Keyset (cursor) pagination on (created_at, id) or a chosen sort key — stable under concurrent inserts
(design §26.1)."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable
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


# --------------------------------------------------------------------------- keyset on a chosen sort key


@dataclass(frozen=True)
class SortKey:
    """A sortable column for keyset pagination: `value` reads it from a row as a string, and `parse`
    turns the cursor string back into a comparable SQL value. The column must be NOT NULL (coalesce)."""

    expr: Any  # an ORM column or SQL expression (e.g. coalesce) to sort and compare on
    value: Callable[[Any], str]
    parse: Callable[[str], Any]


def encode_sort_cursor(value: str, row_id: uuid.UUID) -> str:
    raw = json.dumps({"v": value, "i": str(row_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_sort_cursor(cursor: str, key: SortKey) -> tuple[Any, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        return key.parse(data["v"]), uuid.UUID(data["i"])
    except (ValueError, KeyError, TypeError) as exc:
        raise DomainError("VALIDATION_ERROR", "Invalid cursor", {"field": "cursor"}) from exc


def keyset_sorted(
    query: Select[Any],
    key: SortKey,
    row_id: InstrumentedAttribute[uuid.UUID],
    cursor: str | None,
    limit: int,
    descending: bool,
) -> Select[Any]:
    """(sort key, id) order in either direction; fetch limit+1 to detect a next page."""
    if cursor:
        value, c_id = decode_sort_cursor(cursor, key)
        if descending:
            query = query.where(or_(key.expr < value, and_(key.expr == value, row_id < c_id)))
        else:
            query = query.where(or_(key.expr > value, and_(key.expr == value, row_id > c_id)))
    order = (key.expr.desc(), row_id.desc()) if descending else (key.expr.asc(), row_id.asc())
    return query.order_by(*order).limit(limit + 1)


def to_sorted_page(rows: list[Any], limit: int, key: SortKey) -> Page[Any]:
    if len(rows) <= limit:
        return Page(rows, None)
    last = rows[limit - 1]
    return Page(rows[:limit], encode_sort_cursor(key.value(last), last.id))
