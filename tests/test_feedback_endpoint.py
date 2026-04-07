# pylint: disable=redefined-outer-name

import asyncio
from uuid import UUID
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app, feedback
from app.models.feedback import (
    FEEDBACK_COMMENT_MAX_LENGTH,
    FeedbackClassification,
    FeedbackRequest,
)


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _valid_feedback_form_data(**overrides):
    data = {
        "assessment_id": "1",
        "comment": "Very useful feedback flow",
        "guid": str(uuid4()),
        "reaction_value": "5",
        "feedback_classification": "overall_experience",
    }
    data.update(overrides)
    return data


def test_feedback_handler_calls_create_feedback(monkeypatch: pytest.MonkeyPatch):
    """Test handler success path calls create_feedback and returns 201."""

    called = {}

    def _fake_create_feedback(data):
        called["guid"] = str(data.guid)
        called["reaction_value"] = data.reaction_value
        called["feedback_classification"] = str(data.feedback_classification)

    monkeypatch.setattr("app.main.create_feedback", _fake_create_feedback)
    data = _valid_feedback_form_data()
    request_model = FeedbackRequest(
        assessment_id=int(data["assessment_id"]),
        comment=data["comment"],
        guid=UUID(data["guid"]),
        reaction_value=int(data["reaction_value"]),
        feedback_classification=FeedbackClassification(
            data["feedback_classification"]
        ),
    )

    response = asyncio.run(feedback(request_model))

    assert response.status_code == 201
    assert response.body == b'{"status":"feedback recorded"}'
    assert called == {
        "guid": data["guid"],
        "reaction_value": 5,
        "feedback_classification": "overall_experience",
    }


def test_feedback_endpoint_accepts_valid_payload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test /feedback returns 201 for valid form data."""

    called = {}

    def _fake_create_feedback(data):
        called["guid"] = str(data.guid)
        called["reaction_value"] = data.reaction_value

    monkeypatch.setattr("app.main.create_feedback", _fake_create_feedback)

    response = client.post("/feedback", data=_valid_feedback_form_data())

    assert response.status_code == 201
    assert response.json() == {"status": "feedback recorded"}
    assert called["reaction_value"] == 5


def test_feedback_endpoint_rejects_invalid_guid(client: TestClient):
    """Test /feedback returns 422 for invalid GUID format."""

    response = client.post(
        "/feedback",
        data=_valid_feedback_form_data(guid="not-a-guid"),
    )

    assert response.status_code == 422


def test_feedback_endpoint_rejects_out_of_range_reaction_value(client: TestClient):
    """Test /feedback returns 422 when reaction_value is outside 1..5."""

    response = client.post(
        "/feedback",
        data=_valid_feedback_form_data(reaction_value="6"),
    )

    assert response.status_code == 422


def test_feedback_endpoint_rejects_too_long_comment(client: TestClient):
    """Test /feedback returns 422 when comment exceeds 500 characters."""

    response = client.post(
        "/feedback",
        data=_valid_feedback_form_data(
            comment="a" * (FEEDBACK_COMMENT_MAX_LENGTH + 1)
        ),
    )

    assert response.status_code == 422


def test_feedback_endpoint_rejects_missing_required_fields(client: TestClient):
    """Test /feedback returns 422 when required fields are missing."""

    data = _valid_feedback_form_data()
    data.pop("feedback_classification")

    response = client.post("/feedback", data=data)

    assert response.status_code == 422
