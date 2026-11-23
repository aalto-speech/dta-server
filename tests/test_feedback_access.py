# pylint: disable=redefined-outer-name

"""POST /feedback access checks (consent, assessment ownership), run end to end."""

import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.db import create_assessment
from app.main import app
from app.models.speech_assessment import AssessmentCreateInput


@pytest.fixture
def client():
    """Provide a FastAPI test client on a fresh test database."""

    with TestClient(app) as test_client:
        yield test_client


def _onboard(client: TestClient) -> str:
    guid = str(uuid4())
    response = client.post("/onboarding", data={
        "guid": guid,
        "consent_accepted": "true",
        "consent_timestamp": "2026-01-01T00:00:00Z",
        "finnish_self_assessment": "A1",
    })
    assert response.status_code == 201
    return guid


def _add_assessment(guid: str) -> int:
    return create_assessment(AssessmentCreateInput(
        guid=guid,
        task_id=1,
        audio_id=str(uuid4()),
        audio_path=f"/tmp/{uuid4()}.wav",
        transcript="hei",
        accuracy=2.0, fluency=2.0, proficiency=2.0, pronunciation=2.0, range_score=2.0,
    ))


def _feedback_rows() -> list[tuple]:
    with closing(sqlite3.connect(SETTINGS.database)) as conn:
        return conn.execute("SELECT guid, assessment_id, type FROM feedback").fetchall()


def _rate(client: TestClient, guid: str, assessment_id: int | None = None):
    data = {"guid": guid, "reaction_value": "4"}
    if assessment_id is None:
        data["feedback_classification"] = "overall_experience"
    else:
        data["feedback_classification"] = "result_accuracy"
        data["assessment_id"] = str(assessment_id)
    return client.post("/feedback", data=data)


def test_feedback_on_own_assessment_is_stored(client: TestClient):
    guid = _onboard(client)
    assessment_id = _add_assessment(guid)

    response = _rate(client, guid, assessment_id)

    assert response.status_code == 201
    assert _feedback_rows() == [(guid, assessment_id, "result_accuracy")]


def test_feedback_on_another_users_assessment_is_refused(client: TestClient):
    owner = _onboard(client)
    other = _onboard(client)
    assessment_id = _add_assessment(owner)

    response = _rate(client, other, assessment_id)

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "ASSESSMENT_NOT_FOUND"
    assert _feedback_rows() == []


def test_missing_and_foreign_assessments_look_the_same(client: TestClient):
    """The response must not reveal which assessment ids exist."""

    owner = _onboard(client)
    other = _onboard(client)
    assessment_id = _add_assessment(owner)

    foreign = _rate(client, other, assessment_id)
    missing = _rate(client, other, assessment_id + 1000)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_feedback_from_unknown_guid_is_404_not_a_constraint_error(client: TestClient):
    """Was a 409 DATABASE_CONSTRAINT_ERROR from the foreign key."""

    response = _rate(client, str(uuid4()))

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "USER_NOT_FOUND"
    assert _feedback_rows() == []


def test_feedback_from_user_without_consent_is_refused(client: TestClient):
    """A user row from before onboarding required consent stores no feedback."""

    guid = str(uuid4())
    with closing(sqlite3.connect(SETTINGS.database)) as conn:
        with conn:
            conn.execute(
                "INSERT INTO users (guid, consent_accepted, consent_timestamp, cefr_level) "
                "VALUES (?, 0, '2026-01-01T00:00:00+00:00', 'A1')",
                (guid,),
            )

    response = _rate(client, guid)

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "USER_NOT_FOUND"
    assert _feedback_rows() == []
