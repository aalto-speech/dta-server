from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.config import SETTINGS


class CohortType(StrEnum):
    """Supported comparison cohort definitions.

    Future Extension Points:
    - SELF_ASSESSMENT: Default cohort grouping by user's self-reported Finnish level (CEFR).
    - PERFORMANCE_BAND: (Future) Cohort based on user's inferred performance band from ASA results.

    To add a new cohort type:
    1. Add a new enum member here.
    2. Implement corresponding logic in db.py (e.g., get_comparison_stats_by_performance_band).
    3. Add query parameter to ComparisonQuery to select cohort strategy.
    4. Update analytics endpoint in main.py to route to the appropriate DB helper.
    """

    SELF_ASSESSMENT = "self_assessment"
    # PERFORMANCE_BAND = "performance_band"  # Future sprint


class ComparisonRequest(BaseModel):
    """Request type for comparison analytics endpoint."""

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


class ComparisonStats(BaseModel):
    """Privacy-safe analytics output for user-to-cohort comparison.

    Extension Notes for Future Cohort Types:
    To support additional cohort strategies (e.g., performance band), create parallel
    functions following the pattern of get_comparison_stats_by_self_assessment():

    1. get_comparison_stats_by_performance_band(guid: UUID, days: int | None) -> ComparisonStats
       - Query ASA performance band from assessments (e.g., "above_average", "average", etc).
       - Match user against peers in the same performance band.
       - Apply same privacy gating (minimum cohort size).
       - Return ComparisonStats with cohort_type="performance_band".

    2. Routing in main.py:
       - Accept cohort_type query parameter in analytics endpoint.
       - Route to appropriate DB helper based on selected cohort_type.
       - Default to self_assessment for backward compatibility.

    3. Testing:
       - Mirror existing test suite for new cohort type.
       - Verify privacy boundaries apply equally.
    """

    cohort_label: str
    cohort_size: int
    user_average_score: float | None
    cohort_average: float | None
    percentile: float | None


class ComparisonResponse(BaseModel):
    """Privacy-safe response contract for user-to-cohort comparison.

    Field Adoption Timeline:
    - Phase 3 (Current): cohort_type, cohort_label, cohort_size,
      user_average_score, cohort_average, percentile (primary metric).
    - Phase 5+ (Future): rank_band - human-readable percentile band (e.g., 'top 10%', 'around average').
      The UI can compute rank_band from percentile independently until backend adoption.
    - Phase 5+ (Future): distribution_summary only when privacy guardrails allow (min bucket size).

    Extension Notes on Optional Fields:
    - rankBand: Kept nullable to allow deferred UI adoption. Once UI is ready, endpoint can
      populate this field without breaking existing clients. Derive from percentile using
      configurable band thresholds (e.g., >90→'top 10%', 70-90→'top 30%', etc).
    - distributionSummary: Currently always returned when comparisonAvailable=true. Future
      privacy refinement can suppress if any bucket would contain too few users to be safe
      (e.g., <5). Add config setting MIN_DISTRIBUTION_BUCKET_SIZE to enforce this.
    """

    cohort_label: str
    cohort_size: int = Field(ge=SETTINGS.min_cohort_size)
    cohort_average: float = Field(ge=0, le=5)
    percentile: float = Field(ge=0, le=1)
    # Future: Derive from percentile using configurable bands
    rank_band: str
    # Future: Suppress if bucket < MIN_SIZE

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
