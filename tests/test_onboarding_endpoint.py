# pylint: disable=redefined-outer-name

import asyncio
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app, onboarding
from app.models.onboarding import OnboardingRequest


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _valid_onboarding_form_data(**overrides):
    data = {
        "app_version": "1.0.0",
        "age_group": "age_29_39",
        "finnish_learning_duration": "months_6_9",
        "finnish_self_assessment": "B1",
        "gender": "woman",
        "moved_to_finland": 2012,
        "native_languages": ["Vietnamese"],
        "other_languages": ["English"],
        "background_form_completed": "true",
        "background_form_timestamp": datetime(2026, 1, 2, 3, 4, 5).isoformat(),
        "consent_accepted": "true",
        "consent_timestamp": datetime(2026, 1, 2, 3, 4, 5).isoformat(),
        "guid": str(uuid4()),
    }
    data.update(overrides)
    return data


def test_onboarding_handler_accepts_valid_native_languages(client: TestClient):
    """Test /onboarding accepts valid native_languages as a list."""

    response = client.post(
        "/onboarding",
        data=_valid_onboarding_form_data(
            native_languages="Vietnamese\nFinnish\nEnglish"),
    )

    assert response.status_code == 201


def test_onboarding_handler_calls_create_user(monkeypatch: pytest.MonkeyPatch):
    """Test handler success path calls create_user and returns 201."""

    called = {}
    logged = []

    def _fake_create_user(data):
        called["guid"] = str(data.guid)
        called["moved_to_finland"] = data.moved_to_finland

    monkeypatch.setattr(
        "app.services.onboarding_service.create_user", _fake_create_user)
    monkeypatch.setattr(
        "app.services.onboarding_service.logger.info",
        lambda message, *args: logged.append((message, args)),
    )
    data = _valid_onboarding_form_data()
    request_model = OnboardingRequest(
        app_version=data["app_version"],
        age_group=data["age_group"],
        finnish_learning_duration=data["finnish_learning_duration"],
        finnish_self_assessment=data["finnish_self_assessment"],
        gender=data["gender"],
        moved_to_finland=data["moved_to_finland"],
        native_languages=data["native_languages"],
        other_languages=data["other_languages"],
        background_form_completed=True,
        background_form_timestamp=data["background_form_timestamp"],
        consent_accepted=True,
        consent_timestamp=data["consent_timestamp"],
        guid=data["guid"],
    )

    response = asyncio.run(onboarding(request_model))

    assert response.status_code == 201
    assert called == {
        "guid": data["guid"],
        "moved_to_finland": "before_2015",
    }
    assert logged == [
        ("Created onboarding user %s", (UUID(data["guid"]),)),
    ]


def test_onboarding_endpoint_rejects_invalid_guid(client: TestClient):
    """Test /onboarding returns 422 for invalid guid."""

    response = client.post(
        "/onboarding",
        data=_valid_onboarding_form_data(guid="not-a-guid"),
    )

    assert response.status_code == 422


def test_onboarding_endpoint_rejects_missing_fields(client: TestClient):
    """Test /onboarding returns 422 when required fields are missing."""

    data = _valid_onboarding_form_data()
    data.pop("age_group")

    response = client.post("/onboarding", data=data)

    assert response.status_code == 422


def test_onboarding_endpoint_accepts_moved_to_finland_under_2015(client: TestClient):
    """Test /onboarding accepts moved_to_finland as a year before 2015."""

    response = client.post(
        "/onboarding",
        data=_valid_onboarding_form_data(moved_to_finland=2010),
    )

    assert response.status_code == 201


def test_onboarding_endpoint_accepts_before_2015_string(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Accept literal 'before_2015' for moved_to_finland and pass through unchanged."""

    captured = {}

    def _fake_create_user(data):
        captured["moved_to_finland"] = data.moved_to_finland

    monkeypatch.setattr(
        "app.services.onboarding_service.create_user", _fake_create_user
    )

    response = client.post(
        "/onboarding", data=_valid_onboarding_form_data(moved_to_finland="before_2015")
    )

    assert response.status_code == 201
    assert captured["moved_to_finland"] == "before_2015"


def test_onboarding_endpoint_accepts_string_year(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Accept numeric year provided as a string and normalize to int."""

    captured = {}

    def _fake_create_user(data):
        captured["moved_to_finland"] = data.moved_to_finland

    monkeypatch.setattr(
        "app.services.onboarding_service.create_user", _fake_create_user
    )

    response = client.post(
        "/onboarding", data=_valid_onboarding_form_data(moved_to_finland="2016")
    )

    assert response.status_code == 201
    assert captured["moved_to_finland"] == 2016


def test_onboarding_endpoint_rejects_invalid_moved_to_finland_string(
    monkeypatch: pytest.MonkeyPatch
):
    """Non-numeric, non-'before_2015' strings for moved_to_finland should error at model conversion."""

    monkeypatch.setattr(
        "app.services.onboarding_service.create_user", lambda data: None)

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post(
            "/onboarding", data=_valid_onboarding_form_data(moved_to_finland="not-a-year")
        )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "type": "INTERNAL_SERVER_ERROR",
            "message": "Internal server error"
        }
    }


def test_onboarding_endpoint_rejects_moved_to_finland_too_large(
    monkeypatch: pytest.MonkeyPatch
):
    """Years greater than the allowed max should raise during CreateUserInput validation."""

    monkeypatch.setattr(
        "app.services.onboarding_service.create_user", lambda data: None)

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.post(
            "/onboarding", data=_valid_onboarding_form_data(moved_to_finland=9999)
        )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "type": "INTERNAL_SERVER_ERROR",
            "message": "Internal server error"
        }
    }


def test_onboarding_endpoint_other_languages_string_conversion(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Ensure other_languages string is converted into a list when creating the user.

    Use direct invocation of the route handler with an `OnboardingRequest` instance
    so the pydantic field validator runs on normal model construction.
    """

    captured = {}

    def _fake_create_user(data):
        captured["other_languages"] = data.other_languages

    monkeypatch.setattr(
        "app.services.onboarding_service.create_user", _fake_create_user
    )

    data = _valid_onboarding_form_data(other_languages="English\nSwedish")
    request_model = OnboardingRequest(
        app_version=data["app_version"],
        age_group=data["age_group"],
        finnish_learning_duration=data["finnish_learning_duration"],
        finnish_self_assessment=data["finnish_self_assessment"],
        gender=data["gender"],
        moved_to_finland=data["moved_to_finland"],
        native_languages=data["native_languages"],
        other_languages=data["other_languages"],
        background_form_completed=True,
        background_form_timestamp=data["background_form_timestamp"],
        consent_accepted=True,
        consent_timestamp=data["consent_timestamp"],
        guid=data["guid"],
    )

    response = asyncio.run(onboarding(request_model))

    assert response.status_code == 201
    assert captured["other_languages"] == ["English", "Swedish"]
