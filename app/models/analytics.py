from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.config import SETTINGS


class CohortType(StrEnum):
    """Supported comparison cohort definitions."""

    SELF_ASSESSMENT = "self_assessment"


class ComparisonQuery(BaseModel):
    """Query contract for comparison analytics endpoint."""

    guid: UUID
    days: int | None = Field(default=None, ge=1)

    @field_validator("days")
    @classmethod
    def validate_days_window(cls, value: int | None) -> int | None:
        """Validate optional analytics window against configured bounds."""

        if value is None:
            return value

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


class ComparisonResponse(BaseModel):
    """Privacy-safe response contract for user-to-cohort comparison."""

    comparisonAvailable: bool
    cohortType: CohortType
    cohortLabel: str
    cohortSize: int = Field(ge=0)
    userAverageScore: float | None = Field(default=None, ge=0, le=5)
    cohortAverage: float | None = Field(default=None, ge=0, le=5)
    percentile: float | None = Field(default=None, ge=0, le=100)
    rankBand: str | None = None
    distributionSummary: dict[str, int] | None = None

    @field_validator("percentile")
    @classmethod
    def validate_percentile_if_available(cls, value: float | None) -> float | None:
        """Keep percentile precision stable for client-side rendering."""

        if value is None:
            return value

        return round(value, 2)

    @field_validator("distributionSummary")
    @classmethod
    def validate_distribution_summary(
        cls, value: dict[str, int] | None
    ) -> dict[str, int] | None:
        """Ensure optional distribution buckets cannot contain negative counts."""

        if value is None:
            return value

        for bucket, count in value.items():
            if count < 0:
                raise ValueError(
                    f"distributionSummary bucket '{bucket}' cannot be negative"
                )

        return value
