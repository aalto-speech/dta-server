from fastapi.testclient import TestClient

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
