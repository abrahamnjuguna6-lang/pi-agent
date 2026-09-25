"""FastAPI application entrypoint.

The lifespan builds the composition root (design §4); graphs and the checkpointer join it in T12.7.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lifeos.api.errors import install_error_handlers
from lifeos.api.routers import auth, commitments, goals, me, memory, scheduling
from lifeos.config import get_settings
from lifeos.container import Container, build_container

API_PREFIX = "/api/v1"


def create_app(container: Container | None = None) -> FastAPI:
    """`container` is injected by tests; production builds it from settings in the lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        app.state.container = container or build_container(get_settings())
        app.state.settings = app.state.container.settings
        try:
            yield
        finally:
            if owned:
                await app.state.container.aclose()

    app = FastAPI(title="Personal Life OS", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(me.router, prefix=API_PREFIX)
    app.include_router(goals.router, prefix=API_PREFIX)
    app.include_router(scheduling.router, prefix=API_PREFIX)
    app.include_router(commitments.router, prefix=API_PREFIX)
    app.include_router(memory.router, prefix=API_PREFIX)
    return app


app = create_app()
