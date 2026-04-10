from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator


class Gender(StrEnum):
    """Gender options for onboarding."""

    WOMAN = "woman"
    MAN = "man"
    OTHER = "other"
    PREFER_NOT_TO_ANSWER = "prefer_not_to_answer"


class AgeGroup(StrEnum):
    """Age group options for onboarding."""

    AGE_18_28 = "age_18_28"
    AGE_29_39 = "age_29_39"
    AGE_40_50 = "age_40_50"
    AGE_51_61 = "age_51_61"
    AGE_62_PLUS = "age_62_plus"


class LearningDuration(StrEnum):
    """Learning duration options for onboarding."""

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
    """CEFR level options for onboarding."""

    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1_PLUS = "C1_plus"


MovedToFinland = Literal["before_2015"] | str | int


class OnboardingRequest(BaseModel):
    """Onboarding request payload.

    Attributes:
        app_version: Optional app version.
        age_group: The user's age group.
        finnish_learning_duration: How long the user has been learning Finnish.
        finnish_self_assessment: The user's self-assessed CEFR level in Finnish.
        gender: The user's gender.
        moved_to_finland: Either a year or "before_2015".
        native_languages: The user's native languages.
        other_languages: Other languages the user speaks.
        background_form_completed: Whether the background form was completed.
        background_form_timestamp: When the background form was completed.
        consent_accepted: Whether consent was accepted.
        consent_timestamp: When consent was accepted.
        guid: The user's GUID.
    """

    app_version: str | None = None
    age_group: AgeGroup
    finnish_learning_duration: LearningDuration
    finnish_self_assessment: CEFRLevel
    gender: Gender
    moved_to_finland: MovedToFinland
    native_languages: str | list[str]
    other_languages: str | list[str] | None = None
    background_form_completed: bool
    background_form_timestamp: datetime
    consent_accepted: bool
    consent_timestamp: datetime
    guid: UUID

    @field_validator("native_languages", "other_languages")
    @classmethod
    def validate_languages(cls, value: str | list[str] | None) -> str | list[str] | None:
        """Convert newline-separated language strings into lists."""

        if isinstance(value, str):
            return [lang.strip() for lang in value.split("\n")]

        return value


class CreateUserInput(BaseModel):
    """Internal DB input for creating a user row."""

    app_version: str | None = None
    age_group: AgeGroup
    finnish_learning_duration: LearningDuration
    finnish_self_assessment: CEFRLevel
    gender: Gender
    moved_to_finland: MovedToFinland
    native_languages: str | list[str]
    other_languages: str | list[str] | None = None
    consent_accepted: bool
    consent_timestamp: datetime
    guid: UUID

    @field_validator("moved_to_finland")
    @classmethod
    def validate_moved_to_finland(cls, value: MovedToFinland) -> MovedToFinland:
        """Normalize moved_to_finland into a stored value."""

        if isinstance(value, str) and value == "before_2015":
            return value

        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as exc:
                raise ValueError(
                    "year must be an integer or 'before_2015'") from exc

        # Treat any year before 2015 as "before_2015"
        if value < 2015:
            return "before_2015"

        # Checking an edge case if the server's clock is configured wrong
        max_year = max(datetime.now().year, 2100)

        if value > max_year:
            raise ValueError(f"year must be less than or equal to {max_year}")

        return value
