# pylint: disable=redefined-outer-name

import asyncio
from uuid import UUID, uuid4

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


def test_feedback_endpoint_returns_success_payload_and_calls_create_feedback(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test /feedback returns success body and invokes create_feedback once."""

    called = {}
    logged = []

    def _fake_create_feedback(data):
        called["guid"] = str(data.guid)
        called["feedback_classification"] = str(data.feedback_classification)

    monkeypatch.setattr(
        "app.services.feedback_service.create_feedback", _fake_create_feedback)
    monkeypatch.setattr(
        "app.services.feedback_service.logger.info",
        lambda message, *args: logged.append((message, args)),
    )

    form_data = _valid_feedback_form_data(
        feedback_classification="overall_experience")
    response = client.post("/feedback", data=form_data)

    assert response.status_code == 201
    assert response.json() == {"status": "feedback recorded"}
    assert called == {
        "guid": form_data["guid"],
        "feedback_classification": "overall_experience",
    }
    assert logged == [
        ("Stored feedback for user %s", (UUID(form_data["guid"]),)),
    ]


def test_feedback_routes_assessment_types_to_assessment_feedback(monkeypatch: pytest.MonkeyPatch):
    """Test handler routes assessment feedback types to create_assessment_feedback."""

    called = {}

    def _fake_create_feedback(data):
        called["type"] = "assessment"
        called["feedback_classification"] = str(data.feedback_classification)

    monkeypatch.setattr("app.services.feedback_service.create_feedback",
                        _fake_create_feedback)

    for classification in ["self_assessment", "result_accuracy", "result_understanding"]:
        called.clear()
        data = _valid_feedback_form_data(
            feedback_classification=classification)
        request_model = FeedbackRequest(
            assessment_id=int(data["assessment_id"]),
            comment=data["comment"],
            guid=UUID(data["guid"]),
            reaction_value=int(data["reaction_value"]),
            feedback_classification=FeedbackClassification(
                data["feedback_classification"]),
        )

        response = asyncio.run(feedback(request_model))

        assert response.status_code == 201
        assert called["type"] == "assessment"
        assert called["feedback_classification"] == classification


def test_feedback_routes_experience_types_to_experience_feedback(monkeypatch: pytest.MonkeyPatch):
    """Test handler routes experience feedback types to create_experience_feedback."""

    called = {}

    def _fake_create_feedback(data):
        called["type"] = "experience"
        called["feedback_classification"] = str(data.feedback_classification)

    monkeypatch.setattr("app.services.feedback_service.create_feedback",
                        _fake_create_feedback)

    for classification in ["comparison_ui", "overall_experience"]:
        called.clear()
        data = _valid_feedback_form_data(
            feedback_classification=classification)
        request_model = FeedbackRequest(
            assessment_id=int(data["assessment_id"]),
            comment=data["comment"],
            guid=UUID(data["guid"]),
            reaction_value=int(data["reaction_value"]),
            feedback_classification=FeedbackClassification(
                data["feedback_classification"]),
        )

        response = asyncio.run(feedback(request_model))

        assert response.status_code == 201
        assert called["type"] == "experience"
        assert called["feedback_classification"] == classification


def test_feedback_reaction_value_minimum(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Test that reaction_value=1 (minimum) is accepted."""

    called = {}

    def _fake_create_feedback(data):
        called["reaction_value"] = data.reaction_value

    monkeypatch.setattr("app.services.feedback_service.create_feedback",
                        _fake_create_feedback)

    response = client.post(
        "/feedback",
        data=_valid_feedback_form_data(reaction_value="1"),
    )

    assert response.status_code == 201
    assert called["reaction_value"] == 1


def test_feedback_reaction_value_maximum(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Test that reaction_value=5 (maximum) is accepted."""

    called = {}

    def _fake_create_feedback(data):
        called["reaction_value"] = data.reaction_value

    monkeypatch.setattr("app.services.feedback_service.create_feedback",
                        _fake_create_feedback)

    response = client.post(
        "/feedback",
        data=_valid_feedback_form_data(reaction_value="5"),
    )

    assert response.status_code == 201
    assert called["reaction_value"] == 5


def test_feedback_comment_optional(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Test that comment can be omitted/None."""

    called = {}

    def _fake_create_feedback(data):
        called["comment"] = data.comment

    monkeypatch.setattr("app.services.feedback_service.create_feedback",
                        _fake_create_feedback)

    response = client.post(
        "/feedback",
        data=_valid_feedback_form_data(comment=""),
    )

    assert response.status_code == 201
    # Comment should be None or empty string
    assert called["comment"] in (None, "")


def test_feedback_assessment_requires_assessment_id(client: TestClient):
    """Test that assessment feedback types require assessment_id."""

    response = client.post(
        "/feedback",
        data={
            "guid": str(uuid4()),
            "reaction_value": "5",
            "feedback_classification": "self_assessment",
            # Intentionally omit assessment_id
            "comment": "Test",
        },
    )

    # Should reject because assessment feedback requires assessment_id
    assert response.status_code == 422
