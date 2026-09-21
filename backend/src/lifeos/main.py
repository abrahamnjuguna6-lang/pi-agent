"""FastAPI application entrypoint.

The lifespan will own the DB pool, the LangGraph checkpointer and compiled graphs (T12.7).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lifeos.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.settings = get_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Personal Life OS", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
