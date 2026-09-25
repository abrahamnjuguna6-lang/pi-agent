"""Domain error hierarchy with stable codes (design §26.3).

Domain code raises `DomainError(code, message, details)`; the API layer maps codes to HTTP
statuses and the response envelope. Messages are safe to show users; never put internals here.
"""

from __future__ import annotations

from typing import Any, Literal

ErrorCode = Literal[
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "INVALID_TRANSITION",
    "INVALID_COMMITMENT_TRANSITION",
    "SKIP_REASON_REQUIRED",
    "GOAL_ARCHIVED",
    "CASCADE_CONFIRMATION_REQUIRED",
    "CONFIRMATION_REQUIRED",
    "REQUEST_IN_PROGRESS",
    "IDEMPOTENCY_KEY_REUSED",
    "EMAIL_NOT_VERIFIED",
    "ACCOUNT_LOCKED",
    "RATE_LIMITED",
    "TURN_IN_PROGRESS",
    "AI_UNAVAILABLE",
    # authentication
    "UNAUTHENTICATED",
    "INVALID_CREDENTIALS",
    "INVALID_TOKEN",
    "EMAIL_TAKEN",
    "WEAK_PASSWORD",
    "FORBIDDEN",
]


class DomainError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details: dict[str, Any] = details or {}


class NotFoundError(DomainError):
    """Resource missing OR owned by someone else — indistinguishable externally (design §26.1)."""

    def __init__(self, resource: str = "resource") -> None:
        super().__init__("NOT_FOUND", f"{resource} not found")
