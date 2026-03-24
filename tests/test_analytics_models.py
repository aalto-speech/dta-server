import pytest
from pydantic import ValidationError

from app.config import SETTINGS
from app.models.analytics import ComparisonQuery, ComparisonResponse, CohortType


def test_comparison_query_accepts_days_within_configured_range():
    """Accept valid days values inside configured analytics bounds."""

    query = ComparisonQuery(
        guid="1cfdd122-4422-4f11-bf1f-e7c25d0d77ca",
        days=SETTINGS.analytics_min_window_days,
    )

    assert query.days == SETTINGS.analytics_min_window_days


def test_comparison_query_rejects_days_above_configured_range():
    """Reject days that exceed configured maximum analytics window."""

    with pytest.raises(ValidationError):
        ComparisonQuery(
            guid="4f536be2-5f5f-4310-b24f-9d7d44e91243",
            days=SETTINGS.analytics_max_window_days + 1,
        )


def test_comparison_response_rounds_percentile_to_two_decimals():
    """Round percentile values to a stable precision for consumers."""

    response = ComparisonResponse(
        comparisonAvailable=True,
        cohortType=CohortType.SELF_ASSESSMENT,
        cohortLabel="B1",
        cohortSize=42,
        userAverageScore=3.42,
        cohortAverage=3.1,
        percentile=88.666,
    )

    assert response.percentile == 88.67


def test_comparison_response_rejects_negative_distribution_values():
    """Reject invalid distribution buckets with negative counts."""

    with pytest.raises(ValidationError):
        ComparisonResponse(
            comparisonAvailable=True,
            cohortType=CohortType.SELF_ASSESSMENT,
            cohortLabel="B1",
            cohortSize=42,
            userAverageScore=3.42,
            cohortAverage=3.1,
            percentile=80,
            distributionSummary={"0-1": -1},
        )
