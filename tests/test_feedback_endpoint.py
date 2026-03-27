from fastapi.testclient import TestClient

from app.main import app


def test_feedback():
    """Test the /feedback endpoint."""

    with TestClient(app) as client:
        response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"message": "Pong!"}
