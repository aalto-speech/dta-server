from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from fastapi import Depends, Form
from pydantic import BaseModel, Field, field_validator


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

    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1_PLUS = "C1_plus"


MovedToFinland = Literal["before_2015"] | int


class OnboardingBackgroundFields(BaseModel):
    """Onboarding background fields type"""

    age_group: AgeGroup = Field(...)
    finnish_learning_duration: LearningDuration = Field(...)
    finnish_self_assessment: CEFRLevel = Field(...)
    gender: Gender = Field(...)
    moved_to_finland: MovedToFinland = Field(...)
    native_languages: list[str] = Field(...)
    other_languages: list[str] = Field(...)

    @field_validator("moved_to_finland")
    @classmethod
    def validate_moved_to_finland(cls, value: MovedToFinland) -> MovedToFinland:
        """Validate moved_to_finland against the current year at request time."""

        if value == "before_2015":
            return value

        if value < 2015:
            raise ValueError("year must be greater than or equal to 2015")

        # Checking an edge case if the server's clock is configured wrong
        max_year = max(datetime.now().year, 2100)

        if value > max_year:
            raise ValueError(f"year must be less than or equal to {max_year}")

        return value

    @classmethod
    def as_form(
        cls,
        age_group: AgeGroup = Form(..., alias="background_fields.age_group"),
        finnish_learning_duration: LearningDuration = Form(
            ..., alias="background_fields.finnish_learning_duration"
        ),
        finnish_self_assessment: CEFRLevel = Form(
            ..., alias="background_fields.finnish_self_assessment"
        ),
        gender: Gender = Form(..., alias="background_fields.gender"),
        moved_to_finland: str = Form(...,
                                     alias="background_fields.moved_to_finland"),
        native_languages: list[str] = Form(...,
                                           alias="background_fields.native_languages"),
        other_languages: list[str] = Form(...,
                                          alias="background_fields.other_languages"),
    ) -> "OnboardingBackgroundFields":
        """Build background fields from multipart/form-data."""

        parsed_moved_to_finland: MovedToFinland
        parsed_moved_to_finland = (
            int(moved_to_finland)
            if moved_to_finland.isdigit()
            else moved_to_finland
        )

        return cls(
            age_group=age_group,
            finnish_learning_duration=finnish_learning_duration,
            finnish_self_assessment=finnish_self_assessment,
            gender=gender,
            moved_to_finland=parsed_moved_to_finland,
            native_languages=native_languages,
            other_languages=other_languages,
        )


class OnboardingRequest(BaseModel):
    """Onboarding request type"""

    app_version: str | None = Field(None)
    background_fields: OnboardingBackgroundFields = Field(...)
    consent_accepted: bool = Field(...)
    consent_timestamp: datetime = Field(...)
    guid: UUID = Field(...)

    @classmethod
    def as_form(
        cls,
        app_version: str | None = Form(None),
        consent_accepted: bool = Form(...),
        consent_timestamp: datetime = Form(...),
        guid: UUID = Form(...),
        background_fields: OnboardingBackgroundFields = Depends(
            OnboardingBackgroundFields.as_form
        ),
    ) -> "OnboardingRequest":
        """Build onboarding request from multipart/form-data."""

        return cls(
            app_version=app_version,
            background_fields=background_fields,
            consent_accepted=consent_accepted,
            consent_timestamp=consent_timestamp,
            guid=guid,
        )
