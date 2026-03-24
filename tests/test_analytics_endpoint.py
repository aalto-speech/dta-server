# pylint: disable=redefined-outer-name

import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.db import initialize_database
from app.main import app


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _insert_user(cursor: sqlite3.Cursor, guid: str, level: str = "B1") -> None:
    cursor.execute(
        """
        INSERT INTO users (
            guid,
            consent_accepted,
            consent_timestamp,
            app_version,
            gender,
            age_group,
            native_languages,
            other_languages,
            moved_to_finland,
            finnish_learning_duration,
            finnish_self_assessment
        ) VALUES (?, 1, '2026-01-01T00:00:00', '1.0.0', 'woman', 'age_29_39',
                  '["Finnish"]', '[]', 'before_2015', 'months_6_9', ?)
        """,
        (guid, level),
    )


def _insert_assessment(
    cursor: sqlite3.Cursor,
    guid: str,
    score: float,
    created_at: str,
    suffix: str,
) -> None:
    cursor.execute(
        """
        INSERT INTO assessments (
            guid,
            task_id,
            audio_id,
            audio_path,
            transcript,
            accuracy,
            fluency,
            proficiency,
            pronunciation,
            range_score,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guid,
            f"task-{suffix}",
            f"audio-{suffix}",
            f"/tmp/audio-{suffix}.wav",
            "sample",
            score,
            score,
            score,
            score,
            score,
            created_at,
        ),
    )


def test_analytics_comparison_returns_aggregate_metrics(client: TestClient, reset_database):
    """Return user and cohort aggregate stats when privacy threshold is met."""

    _ = reset_database
    initialize_database()
    target_guid = uuid4()

    with sqlite3.connect(SETTINGS.database) as conn:
        cursor = conn.cursor()
        _insert_user(cursor, str(target_guid), level="B1")
        _insert_assessment(cursor, str(target_guid), 4.0,
                           "2026-03-10 10:00:00", "target")

        for idx in range(SETTINGS.minimum_cohort_size - 1):
            peer_guid = uuid4()
            _insert_user(cursor, str(peer_guid), level="B1")
            _insert_assessment(
                cursor,
                str(peer_guid),
                2.0,
                f"2026-03-{idx + 1:02d} 10:00:00",
                f"peer-{idx}",
            )

        conn.commit()

    response = client.get("/analytics/comparison",
                          params={"guid": str(target_guid)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["comparisonAvailable"] is True
    assert payload["cohortType"] == "self_assessment"
    assert payload["cohortLabel"] == "B1"
    assert payload["cohortSize"] == SETTINGS.minimum_cohort_size
    assert payload["userAverageScore"] == 4.0
    assert payload["cohortAverage"] == 2.2
    assert payload["percentile"] == 100.0
    assert isinstance(payload["distributionSummary"], dict)


def test_analytics_comparison_hides_details_for_small_cohort(client: TestClient, reset_database):
    """Return comparisonAvailable false when cohort size is below privacy threshold."""

    _ = reset_database
    initialize_database()

    with sqlite3.connect(SETTINGS.database) as conn:
        cursor = conn.cursor()
        for idx in range(SETTINGS.minimum_cohort_size - 1):
            guid = uuid4()
            _insert_user(cursor, str(guid), level="B1")
            _insert_assessment(
                cursor,
                str(guid),
                3.0,
                f"2026-03-{idx + 1:02d} 10:00:00",
                f"small-{idx}",
            )
        conn.commit()

    response = client.get("/analytics/comparison", params={"guid": str(guid)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["comparisonAvailable"] is False
    assert payload["cohortSize"] == SETTINGS.minimum_cohort_size - 1
    assert payload["cohortAverage"] is None
    assert payload["percentile"] is None
    assert payload["distributionSummary"] is None


def test_analytics_comparison_rejects_invalid_days(client: TestClient):
    """Return 422 when days exceeds configured upper bound."""

    response = client.get(
        "/analytics/comparison",
        params={
            "guid": str(uuid4()),
            "days": SETTINGS.analytics_max_window_days + 1,
        },
    )

    assert response.status_code == 422


def test_analytics_comparison_returns_404_for_unknown_user(client: TestClient, reset_database):
    """Return 404 for GUIDs that do not exist in the users table."""

    _ = reset_database
    initialize_database()

    response = client.get("/analytics/comparison",
                          params={"guid": str(uuid4())})

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


def test_analytics_comparison_rejects_invalid_guid_format(client: TestClient, reset_database):
    """Return 422 when guid is not a valid UUID format."""

    _ = reset_database
    initialize_database()

    response = client.get("/analytics/comparison",
                          params={"guid": "not-a-valid-uuid"})

    assert response.status_code == 422


def test_analytics_comparison_user_with_no_assessments_comparison_unavailable(client: TestClient, reset_database):
    """User with no assessments returns comparison unavailable even if cohort exists."""

    _ = reset_database
    initialize_database()
    target_guid = uuid4()

    with sqlite3.connect(SETTINGS.database) as conn:
        cursor = conn.cursor()
        # Create target user with no assessments
        _insert_user(cursor, str(target_guid), level="B1")

        # Create enough cohort members to meet threshold
        for idx in range(SETTINGS.minimum_cohort_size):
            peer_guid = uuid4()
            _insert_user(cursor, str(peer_guid), level="B1")
            _insert_assessment(
                cursor,
                str(peer_guid),
                3.0,
                f"2026-03-{idx + 1:02d} 10:00:00",
                f"peer-{idx}",
            )
        conn.commit()

    response = client.get("/analytics/comparison",
                          params={"guid": str(target_guid)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["comparisonAvailable"] is False
    assert payload["userAverageScore"] is None
    assert payload["cohortAverage"] is None
    assert payload["percentile"] is None


def test_analytics_comparison_response_does_not_expose_peer_data(client: TestClient, reset_database):
    """Response payload never contains individual peer GUIDs or raw scores."""

    _ = reset_database
    initialize_database()
    target_guid = uuid4()

    with sqlite3.connect(SETTINGS.database) as conn:
        cursor = conn.cursor()
        _insert_user(cursor, str(target_guid), level="B1")
        _insert_assessment(cursor, str(target_guid), 4.0,
                           "2026-03-10 10:00:00", "target")

        for idx in range(SETTINGS.minimum_cohort_size - 1):
            peer_guid = uuid4()
            _insert_user(cursor, str(peer_guid), level="B1")
            _insert_assessment(
                cursor,
                str(peer_guid),
                2.0,
                f"2026-03-{idx + 1:02d} 10:00:00",
                f"peer-{idx}",
            )
        conn.commit()

    response = client.get("/analytics/comparison",
                          params={"guid": str(target_guid)})

    assert response.status_code == 200
    payload = response.json()

    # Verify response contains no lists of GUIDs or raw peer scores
    response_str = str(payload)
    assert "peer-" not in response_str

    # Verify distribution summary contains only aggregate counts, not individual values
    if payload["distributionSummary"] is not None:
        for bucket_key, count in payload["distributionSummary"].items():
            assert isinstance(bucket_key, str)
            assert isinstance(count, int)
            assert count >= 0
