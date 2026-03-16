from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_onboarding_accepts_valid_payload():
    """Test the /onboarding endpoint with a valid request payload."""

    payload = {
        "app_version": "1.0.0",
        "background_fields": {
            "age_group": "age_29_39",
            "gender": "woman",
            "learning_duration": "years_1_1.5",
            "moved_to_finland": 2020,
            "native_languages": ["fi"],
            "other_languages": ["en"],
            "self_assessment": "a2",
        },
        "consent_timestamp": "2026-03-16T12:34:56Z",
        "guid": "550e8400-e29b-41d4-a716-446655440000",
    }

    response = client.post("/onboarding", json=payload)
    assert response.status_code == 200
    expected_payload = payload
    assert response.json() == {"payload": expected_payload}


def test_onboarding_rejects_missing_payload():
    """Test that /onboarding validates required request body."""

    response = client.post("/onboarding")
    assert response.status_code == 422
