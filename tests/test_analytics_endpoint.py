# pylint: disable=redefined-outer-name

from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.main import app
from app.models.analytics import (
    AssessmentUnavailable,
    ComparisonStats,
    DayWindow,
    CohortSizeTooLow,
    GetCohortStatsInput,
)
from app.models.onboarding import CEFRLevel


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _valid_form_data(**overrides: str | int) -> dict[str, str]:
    data: dict[str, str] = {
        "guid": str(uuid4()),
    }
    data.update({key: str(value) for key, value in overrides.items()})
    return data


def _noop_validate_user_access(_guid: object) -> None:
    return None


def _noop_get_cohort_stats(_data: GetCohortStatsInput) -> CohortSizeTooLow:
    return CohortSizeTooLow(
        status="COHORT_SIZE_TOO_SMALL",
        message="Comparison statistics are not available for your cohorts size at this time.",
        cohort_size=0,
    )


def test_analytics_comparison_returns_stats_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Return serialized comparison payload when stats are available."""

    captured = {}

    def _fake_validate_user_access(guid: object) -> None:
        captured["guid"] = str(guid)

    def _fake_get_cohort_stats(data: GetCohortStatsInput) -> ComparisonStats:
        captured["get_stats_guid"] = str(data.guid)
        captured["days"] = data.days
        return ComparisonStats(
            cefr_level=CEFRLevel.B1,
            cohort_size=SETTINGS.min_cohort_size,
            percentile=0.83,
            rank=1,
        )

    monkeypatch.setattr("app.services.analytics_service.auth.validate_user_access",
                        _fake_validate_user_access)
    monkeypatch.setattr(
        "app.services.analytics_service.get_cohort_stats", _fake_get_cohort_stats)

    form_data = _valid_form_data()
    response = client.post("/analytics/comparison", data=form_data)

    assert response.status_code == 200
    assert response.json() == {
        "cefr_level": "B1",
        "cohort_size": SETTINGS.min_cohort_size,
        "percentile": 0.83,
        "rank": 1,
    }
    assert captured == {
        "guid": form_data["guid"],
        "get_stats_guid": form_data["guid"],
        "days": DayWindow.ALL_TIME,
    }


def test_analytics_comparison_returns_comparison_unavailable_when_no_stats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Return comparison_unavailable payload when cohort stats are not available."""

    monkeypatch.setattr(
        "app.services.analytics_service.auth.validate_user_access",
        _noop_validate_user_access,
    )
    monkeypatch.setattr(
        "app.services.analytics_service.get_cohort_stats",
        _noop_get_cohort_stats,
    )

    response = client.post("/analytics/comparison", data=_valid_form_data())

    assert response.status_code == 200
    assert response.json() == {
        "status": "COHORT_SIZE_TOO_SMALL",
        "message": "Comparison statistics are not available for your cohorts size at this time.",
        "cohort_size": 0,
    }


def test_analytics_comparison_returns_insufficient_assessment_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Return business-status payload when user has too few assessments."""

    def _insufficient_assessments(_data: GetCohortStatsInput) -> AssessmentUnavailable:
        return AssessmentUnavailable(
            status="USER_ASSESSMENT_DATA_INSUFFICIENT",
            message="User does not have enough scored assessments for comparison statistics",
            required_assessments=3,
            current_assessments=2,
        )

    monkeypatch.setattr(
        "app.services.analytics_service.auth.validate_user_access",
        _noop_validate_user_access,
    )
    monkeypatch.setattr(
        "app.services.analytics_service.get_cohort_stats",
        _insufficient_assessments,
    )

    response = client.post("/analytics/comparison", data=_valid_form_data())

    assert response.status_code == 200
    assert response.json() == {
        "status": "USER_ASSESSMENT_DATA_INSUFFICIENT",
        "message": "User does not have enough scored assessments for comparison statistics",
        "required_assessments": 3,
        "current_assessments": 2,
    }


def test_analytics_comparison_returns_404_when_auth_rejects_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Propagate auth layer 404 when user does not exist."""

    def _raise_not_found(_guid: object) -> None:
        raise HTTPException(status_code=404, detail="User not found")

    monkeypatch.setattr(
        "app.services.analytics_service.auth.validate_user_access",
        _raise_not_found,
    )

    response = client.post("/analytics/comparison", data=_valid_form_data())

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}
