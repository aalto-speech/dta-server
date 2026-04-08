from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.config import SETTINGS
from app.models.onboarding import CEFRLevel


class ComparisonRequest(BaseModel):
    """Request type for comparison analytics endpoint."""

    guid: UUID
    # ? Should `days` be specified by a fixed set of options instead of an open integer?
    days: int = Field(default=30)

    @field_validator("days")
    @classmethod
    def validate_days_window(cls, value: int) -> int:
        """Validate optional analytics window against configured bounds."""

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
    """Internal data model for comparison statistics and percentile rank."""

    cefr_level: CEFRLevel
    cohort_size: int
    percentile: float
    rank: int


class ComparisonResponse(BaseModel):
    """Comparison response type for returning cohort statistics and user's percentile rank.

    Attributes:
        cefr_level: CEFR level label for the user's cohort.
        cohort_size: Number of users in the cohort used for comparison.
        percentile: User's percentile rank within the cohort (0-1).
        rank: User's rank.
    """

    cefr_level: CEFRLevel
    cohort_size: int = Field(ge=SETTINGS.min_cohort_size)
    percentile: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)

    @field_validator("percentile")
    @classmethod
    def validate_percentile_if_available(cls, value: float | None) -> float | None:
        """Keep percentile precision stable for client-side rendering."""

        if value is None:
            return value

        return round(value, 2)

    @field_validator("cohort_size")
    @classmethod
    def normalize_cohort_size(cls, value: int) -> int:
        """Enforce non-negative cohort size."""

        if value < 0:
            raise ValueError("cohort_size cannot be negative")

        return value
