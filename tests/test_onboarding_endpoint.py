# pylint: disable=redefined-outer-name

import asyncio
import json
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app, onboarding
from app.models.onboarding import OnboardingRequest


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _valid_onboarding_form_payload(**overrides):
    payload = {
        "app_version": "1.0.0",
        "background_fields": json.dumps(
            {
                "age_group": "age_29_39",
                "finnish_learning_duration": "months_6_9",
                "finnish_self_assessment": "B1",
                "gender": "woman",
                "moved_to_finland": 2012,
                "native_languages": ["Vietnamese"],
                "other_languages": ["English"],
            }
        ),
        "background_form_completed": "true",
        "background_form_timestamp": datetime(2026, 1, 2, 3, 4, 5).isoformat(),
        "consent_accepted": "true",
        "consent_timestamp": datetime(2026, 1, 2, 3, 4, 5).isoformat(),
        "guid": str(uuid4()),
    }
    payload.update(overrides)
    return payload


def test_onboarding_handler_calls_create_user(monkeypatch: pytest.MonkeyPatch):
    """Test handler success path calls create_user and returns 201."""

    called = {}

    def _fake_create_user(data):
        called["guid"] = str(data.guid)
        called["moved_to_finland"] = data.background_fields.moved_to_finland

    monkeypatch.setattr("app.main.create_user", _fake_create_user)
    payload = _valid_onboarding_form_payload()
    request_model = OnboardingRequest(
        app_version=payload["app_version"],
        background_fields=json.loads(payload["background_fields"]),
        consent_accepted=True,
        consent_timestamp=payload["consent_timestamp"],
        guid=payload["guid"],
    )

    response = asyncio.run(onboarding(request_model))

    assert response.status_code == 201
    assert called == {
        "guid": payload["guid"],
        "moved_to_finland": "before_2015",
    }


def test_onboarding_endpoint_rejects_invalid_guid(client: TestClient):
    """Test /onboarding returns 422 for invalid guid."""

    response = client.post(
        "/onboarding",
        data=_valid_onboarding_form_payload(guid="not-a-guid"),
    )

    assert response.status_code == 422


def test_onboarding_endpoint_rejects_missing_background_fields(client: TestClient):
    """Test /onboarding returns 422 when required fields are missing."""

    payload = _valid_onboarding_form_payload()
    payload.pop("background_fields")

    response = client.post("/onboarding", data=payload)

    assert response.status_code == 422
