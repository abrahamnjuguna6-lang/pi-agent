"""T0.1: the API starts and serves the health endpoint."""

from fastapi.testclient import TestClient

from lifeos.main import create_app


def test_healthz(test_env: dict[str, str]) -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
