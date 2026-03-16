from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class SpeechAssessmentScores(BaseModel):
    """Speech assessment score type"""

    accuracy: float
    fluency: float
    proficiency: float
    pronunciation: float
    range: float


class SpeechAssessment(BaseModel):
    """Speech assessment result type"""

    scores: SpeechAssessmentScores
    transcript: str


class Gender(StrEnum):
    """Gender for onboarding background fields"""

    WOMAN = "woman"
    MAN = "man"
    OTHER = "other"
    PREFER_NOT_TO_ANSWER = "prefer_not_to_answer"


class AgeGroup(StrEnum):
    """Age group for onboarding background fields"""

    AGE_18_28 = "age_18_28"
    AGE_29_39 = "age_29_39"
    AGE_40_50 = "age_40_50"
    AGE_51_61 = "age_51_61"
    AGE_62_PLUS = "age_62_plus"


class LearningDuration(StrEnum):
    """Duration of time learned Finnish for onboarding background fields"""

    MONTHS_0_TO_3 = "months_0_3"
    MONTHS_3_TO_6 = "months_3_6"
    MONTHS_6_TO_9 = "months_6_9"
    MONTHS_9_TO_12 = "months_9_12"
    YEARS_1_TO_1_HALFS = "years_1_1.5"
    YEARS_1_HALFS_TO_2 = "years_1.5_2"
    YEARS_2_TO_2_HALFS = "years_2_2.5"
    YEARS_2_HALFS_TO_3 = "years_2.5_3"
    YEARS_3_TO_5 = "years_3_5"
    YEARS_5_TO_7 = "years_5_7"
    YEARS_7_TO_10 = "years_7_10"
    YEARS_10_PLUS = "years_10_plus"


class CEFRLevel(StrEnum):
    """CEFR assessment levels for onboarding background fields"""

    A1 = "a1"
    A2 = "a2"
    B1 = "b1"
    B2 = "b2"
    C1_PLUS = "c1_plus"


MovedToFinland = Literal["before_2015"] | Annotated[int, Field(ge=2015)]


class OnboardingBackgroundFields(BaseModel):
    """Onboarding background fields type"""

    age_group: AgeGroup
    gender: Gender
    learning_duration: LearningDuration
    moved_to_finland: MovedToFinland
    native_languages: list[str]
    other_languages: list[str]
    self_assessment: CEFRLevel

    @field_validator("moved_to_finland")
    @classmethod
    def validate_moved_to_finland(cls, value: MovedToFinland) -> MovedToFinland:
        """Validate moved_to_finland against the current year at request time."""

        if value == "before_2015":
            return value

        # Checking an edge case if the server's clock is configured wrong
        max_year = max(datetime.now().year, 2100)

        if value > max_year:
            raise ValueError(f"year must be less than or equal to {max_year}")

        return value


class OnboardingRequest(BaseModel):
    """Onboarding request type"""

    app_version: str
    background_fields: OnboardingBackgroundFields | None  # ! None allowed temporarily
    consent_timestamp: datetime
    guid: UUID
