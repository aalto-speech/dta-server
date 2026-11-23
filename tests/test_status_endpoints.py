from fastapi.testclient import TestClient

import pytest

from app.config import AppEnv, api_docs_urls
from app.main import app


def test_ping():
    """Test the /ping endpoint."""

    with TestClient(app) as client:
        response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"message": "Pong!"}


def test_status():
    """Test the /status endpoint."""

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["env"], str)
    assert isinstance(payload["uptime_seconds"], (int, float))
    assert payload["uptime_seconds"] >= 0


@pytest.mark.parametrize("env", [AppEnv.DEVELOPMENT, AppEnv.TEST, AppEnv.STAGING])
def test_api_docs_are_served_outside_production(env: AppEnv):
    """Client developers generate their client from these on development and staging."""

    assert api_docs_urls(env) == {
        "docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


def test_api_docs_are_not_served_in_production():
    """The docs and schema map the whole API; production does not hand that out."""

    assert api_docs_urls(AppEnv.PRODUCTION) == {
        "docs_url": None, "redoc_url": None, "openapi_url": None}


def test_test_server_serves_the_schema():
    """The test environment serves /openapi.json."""

    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/docs").status_code == 200
