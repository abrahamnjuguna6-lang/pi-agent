"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from lifeos.container import Container
from lifeos.domain.auth.sessions import AuthContext
from lifeos.domain.errors import DomainError


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


async def current_user(request: Request, container: ContainerDep) -> AuthContext:
    """Validate the bearer token AND the session version on every request (design §21.2)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise DomainError("UNAUTHENTICATED", "Authentication required")
    return await container.session_validator.authenticate(token.strip())


CurrentUser = Annotated[AuthContext, Depends(current_user)]


def client_ip(request: Request) -> str:
    # Trusted-proxy header handling is configured at deployment (T18.3); direct peer otherwise.
    return request.client.host if request.client else "unknown"
