"""T3.1: error envelope, status mapping, and no internals leaked."""

from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from lifeos.api.errors import STATUS, install_error_handlers
from lifeos.domain.errors import DomainError, ErrorCode, NotFoundError


class Body(BaseModel):
    n: int


def make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/domain/{code}")
    async def domain(code: str) -> None:
        raise DomainError(code, "safe message", {"hint": 1})  # type: ignore[arg-type]

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("goal")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("SELECT * FROM users -- secret internals")

    @app.post("/validate")
    async def validate(body: Body) -> dict[str, int]:
        return {"n": body.n}

    return app


client = TestClient(make_app(), raise_server_exceptions=False)


def test_every_error_code_has_an_http_status() -> None:
    assert set(get_args(ErrorCode)) == set(STATUS)


@pytest.mark.parametrize("code", sorted(STATUS))
def test_domain_error_envelope(code: str) -> None:
    r = client.get(f"/domain/{code}")
    assert r.status_code == STATUS[code]  # type: ignore[index]
    body = r.json()
    assert body["error"] == {"code": code, "message": "safe message", "details": {"hint": 1}}
    assert set(body["meta"]) == {"request_id", "timestamp"}


def test_not_found_is_generic() -> None:
    r = client.get("/missing")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "NOT_FOUND")


def test_unhandled_exception_hides_internals() -> None:
    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "SELECT" not in r.text
    assert "Traceback" not in r.text


def test_request_validation_uses_envelope() -> None:
    r = client.post("/validate", json={"n": "not-an-int"})
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"][0]["loc"] == ["body", "n"]


def test_unknown_route_uses_envelope() -> None:
    r = client.get("/nope")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "NOT_FOUND")
