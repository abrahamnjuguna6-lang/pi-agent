"""HTTP binding for idempotent mutations (design §22, §26.1).

Usage in a mutating route:

    @router.post("/goals", status_code=201)
    async def create_goal(body: GoalIn, request: Request, user: CurrentUser, key: IdempotencyKey,
                          c: ContainerDep) -> JSONResponse:
        async def op(s: AsyncSession) -> StoredResult:
            goal = await c.goals.create(s, user.user_id, body)
            return StoredResult(201, envelope(request, goal))
        return await idempotent(request, c, user, key, body.model_dump(mode="json"), op)
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from lifeos.container import Container
from lifeos.domain.auth.sessions import AuthContext
from lifeos.domain.errors import DomainError
from lifeos.domain.idempotency import Operation, fingerprint

_KEY_RE = re.compile(r"^[A-Za-z0-9_\-:.]{8,200}$")
REPLAY_HEADER = "Idempotent-Replayed"


def require_idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key")
    if key is None:
        raise DomainError(
            "VALIDATION_ERROR",
            "Idempotency-Key header is required for this request",
            {"header": "Idempotency-Key"},
        )
    if not _KEY_RE.match(key):
        raise DomainError(
            "VALIDATION_ERROR",
            "Idempotency-Key must be 8–200 characters of letters, digits, '-', '_', ':' or '.'",
            {"header": "Idempotency-Key"},
        )
    return key


IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]


async def idempotent(
    request: Request, container: Container, user: AuthContext, key: str, body: Any, op: Operation
) -> JSONResponse:
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    fp = fingerprint(request.method, route_path + "?" + str(sorted(request.path_params.items())), body)
    outcome = await container.idempotency.run(user.user_id, key, fp, op)
    headers = {REPLAY_HEADER: "true"} if outcome.replayed else None
    return JSONResponse(status_code=outcome.result.status_code, content=outcome.result.body, headers=headers)
