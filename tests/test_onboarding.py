import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.usefixtures("reset_database")


def test_onboarding_accepts_valid_payload():
    """Test the /onboarding endpoint with a valid form payload."""

    payload = {
        "app_version": "1.0.0",
        "background_fields.age_group": "age_18_28",
        "background_fields.finnish_learning_duration": "months_0_3",
        "background_fields.finnish_self_assessment": "A1",
        "background_fields.gender": "woman",
        "background_fields.moved_to_finland": "before_2015",
        "background_fields.native_languages": ["Vietnamese"],
        "background_fields.other_languages": ["English"],
        "consent_accepted": "true",
        "consent_timestamp": "2026-03-18T12:12:45.988Z",
        "guid": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    }

    with TestClient(app) as client:
        response = client.post("/onboarding", data=payload)
    assert response.status_code == 200


def test_onboarding_rejects_missing_payload():
    """Test that /onboarding validates required request body."""

    with TestClient(app) as client:
        response = client.post("/onboarding")
    assert response.status_code == 422
