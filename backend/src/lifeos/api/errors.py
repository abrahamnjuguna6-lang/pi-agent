"""Response envelope and exception handlers (design §26.1, §26.3; tasks T3.1)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from lifeos.domain.errors import DomainError, ErrorCode

log = logging.getLogger(__name__)

STATUS: dict[ErrorCode, int] = {
    "VALIDATION_ERROR": 422,
    "WEAK_PASSWORD": 422,
    "SKIP_REASON_REQUIRED": 422,
    "IDEMPOTENCY_KEY_REUSED": 422,
    "CONFIRMATION_REQUIRED": 400,
    "NOT_FOUND": 404,
    "INVALID_TRANSITION": 409,
    "INVALID_COMMITMENT_TRANSITION": 409,
    "GOAL_ARCHIVED": 409,
    "CASCADE_CONFIRMATION_REQUIRED": 409,
    "REQUEST_IN_PROGRESS": 409,
    "TURN_IN_PROGRESS": 409,
    "EMAIL_TAKEN": 409,
    "EMAIL_NOT_VERIFIED": 403,
    "FORBIDDEN": 403,
    "ACCOUNT_LOCKED": 423,
    "RATE_LIMITED": 429,
    "UNAUTHENTICATED": 401,
    "INVALID_CREDENTIALS": 401,
    "INVALID_TOKEN": 401,
    "AI_UNAVAILABLE": 503,
}


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = str(uuid.uuid4())
        request.state.request_id = rid
    return rid


def meta(request: Request, **extra: Any) -> dict[str, Any]:
    return {"request_id": request_id(request), "timestamp": datetime.now(UTC).isoformat(), **extra}


def envelope(request: Request, data: Any, **meta_extra: Any) -> dict[str, Any]:
    return {"data": data, "meta": meta(request, **meta_extra)}


def error_response(
    request: Request, status: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "details": details or {}},
            "meta": meta(request),
        },
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(request, STATUS.get(exc.code, 400), exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"loc": [str(p) for p in err.get("loc", ())], "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return error_response(
            request, 422, "VALIDATION_ERROR", "Request validation failed", {"fields": fields}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 405: "VALIDATION_ERROR"}.get(
            exc.status_code, "VALIDATION_ERROR"
        )
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error request_id=%s", request_id(request))
        return error_response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred")
