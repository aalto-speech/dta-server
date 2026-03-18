from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_onboarding_accepts_valid_payload():
    """Test the /onboarding endpoint with a valid request payload."""

    payload = {
        "app_version": "1.0.0",
        "background_fields": {
            "age_group": "18-28",
            "finnish_learning_duration": "months_0_3",
            "finnish_self_assessment": "A1",
            "gender": "woman",
            "moved_to_finland": "before_2015",
            "native_languages": [
                "Vietnamese"
            ],
            "other_languages": [
                "English"
            ]
        },
        "consent_accepted": True,
        "consent_timestamp": "2026-03-18T12:12:45.988Z",
        "guid": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    }

    response = client.post("/onboarding", json=payload)
    assert response.status_code == 200


def test_onboarding_rejects_missing_payload():
    """Test that /onboarding validates required request body."""

    response = client.post("/onboarding")
    assert response.status_code == 422
