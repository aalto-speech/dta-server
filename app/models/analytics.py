from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.config import SETTINGS
from app.models.onboarding import CEFRLevel


class DayWindow(Enum):
    """Day window options for cohort comparisons."""

    FORTNIGHT = 14
    MONTH = 30
    QUARTER = 90
    HALF_YEAR = 180
    YEAR = 365
    TWO_YEARS = 730
    FOUR_YEARS = 1460
    ALL_TIME = None


class ComparisonRequest(BaseModel):
    """Comparison analytics request payload.

    Attributes:
        guid: The user's GUID.
        days: Optional day window for comparison cohorts, or null for all-time.
    """

    guid: UUID
    days: DayWindow = Field(default=DayWindow.ALL_TIME)


class ComparisonStats(BaseModel):
    """Internal comparison statistics model."""

    cefr_level: CEFRLevel
    cohort_size: int
    percentile: float
    rank: int


class ComparisonUnavailable(BaseModel):
    """Internal model for comparison-unavailable business states."""

    status: str
    message: str


class AssessmentUnavailable(ComparisonUnavailable):
    """Internal model for insufficient-assessment business state."""

    required_assessments: int | None = None
    current_assessments: int | None = None


class CohortSizeTooLow(ComparisonUnavailable):
    """Internal model for insufficient-cohort-size business state."""

    cohort_size: int | None = None


class NoRankAvailable(ComparisonUnavailable):
    """Internal model for unavailable rank within cohort business state."""


class GetCohortStatsInput(BaseModel):
    """Internal DB input for cohort statistics lookup."""

    guid: UUID
    days: DayWindow = DayWindow.ALL_TIME


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
