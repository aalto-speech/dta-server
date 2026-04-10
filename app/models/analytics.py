from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.config import SETTINGS
from app.models.onboarding import CEFRLevel


class ComparisonRequest(BaseModel):
    """Comparison analytics request payload.

    Attributes:
        guid: The user's GUID.
        days: Rolling analytics window in days.
    """

    guid: UUID
    # ? Should `days` be specified by a fixed set of options instead of an open integer?
    days: int | None = Field(default=30)

    @field_validator("days")
    @classmethod
    def validate_days_window(cls, value: int | None) -> int | None:
        """Validate the analytics window against configured bounds."""

        if not value:
            return 30  # Default to 30 days if not provided

        if value < SETTINGS.analytics_min_window_days:
            raise ValueError(
                "days must be greater than or equal to "
                f"{SETTINGS.analytics_min_window_days}"
            )

        if value > SETTINGS.analytics_max_window_days:
            raise ValueError(
                "days must be less than or equal to "
                f"{SETTINGS.analytics_max_window_days}"
            )

        return value


class ComparisonStats(BaseModel):
    """Internal comparison statistics model."""

    cefr_level: CEFRLevel
    cohort_size: int
    percentile: float
    rank: int


class GetCohortStatsInput(BaseModel):
    """Internal DB input for cohort statistics lookup."""

    guid: UUID
    days: int | None = None


class ComparisonResponse(BaseModel):
    """Comparison response payload.

    Attributes:
        cefr_level: CEFR level label for the user's cohort.
        cohort_size: Number of users in the cohort used for comparison.
        percentile: User's percentile rank within the cohort.
        rank: User's rank.
    """

    cefr_level: CEFRLevel
    cohort_size: int = Field(ge=SETTINGS.min_cohort_size)
    percentile: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)

    @field_validator("percentile")
    @classmethod
    def validate_percentile_if_available(cls, value: float | None) -> float | None:
        """Round percentile values for stable rendering."""

        if value is None:
            return value

        return round(value, 2)

    @field_validator("cohort_size")
    @classmethod
    def normalize_cohort_size(cls, value: int) -> int:
        """Reject negative cohort sizes."""

        if value < 0:
            raise ValueError("cohort_size cannot be negative")

        return value
