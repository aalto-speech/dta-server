# pylint: disable=redefined-outer-name

from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import SETTINGS
from app.main import app
from app.models.analytics import ComparisonStats


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _valid_form_data(**overrides):
    data = {
        "guid": str(uuid4()),
        "days": 30,
    }
    data.update(overrides)
    return data


def test_analytics_comparison_returns_stats_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Return serialized comparison payload when stats are available."""

    captured = {}

    def _fake_validate_user_access(guid):
        captured["guid"] = str(guid)

    def _fake_get_cohort_stats(guid, days):
        captured["get_stats_guid"] = str(guid)
        captured["days"] = days
        return ComparisonStats(
            cefr_level="B1",
            cohort_size=SETTINGS.min_cohort_size,
            percentile=0.83,
            rank=1,
        )

    monkeypatch.setattr("app.services.analytics_service.auth.validate_user_access",
                        _fake_validate_user_access)
    monkeypatch.setattr(
        "app.services.analytics_service.get_cohort_stats", _fake_get_cohort_stats)

    form_data = _valid_form_data(days=14)
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
        "days": 14,
    }


def test_analytics_comparison_returns_comparison_unavailable_when_no_stats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Return comparison_unavailable payload when cohort stats are not available."""

    monkeypatch.setattr(
        "app.services.analytics_service.auth.validate_user_access", lambda guid: None)
    monkeypatch.setattr(
        "app.services.analytics_service.get_cohort_stats", lambda guid, days: None)

    response = client.post("/analytics/comparison", data=_valid_form_data())

    assert response.status_code == 200
    assert response.json() == {
        "status": "comparison_unavailable",
        "message": "Comparison statistics are not available for your cohorts size at this time.",
    }


def test_analytics_comparison_returns_404_when_auth_rejects_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Propagate auth layer 404 when user does not exist."""

    def _raise_not_found(_guid):
        raise HTTPException(status_code=404, detail="User not found")

    monkeypatch.setattr(
        "app.services.analytics_service.auth.validate_user_access", _raise_not_found)

    response = client.post("/analytics/comparison", data=_valid_form_data())

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


def test_analytics_comparison_rejects_invalid_guid_format(client: TestClient):
    """Return 422 when guid is not a valid UUID string."""

    response = client.post(
        "/analytics/comparison",
        data=_valid_form_data(guid="not-a-valid-uuid"),
    )

    assert response.status_code == 422


def test_analytics_comparison_rejects_days_above_max(client: TestClient):
    """Return 422 when days exceeds configured upper bound."""

    response = client.post(
        "/analytics/comparison",
        data=_valid_form_data(days=SETTINGS.analytics_max_window_days + 1),
    )

    assert response.status_code == 422
