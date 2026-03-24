
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class FeedbackType(StrEnum):
    """Feedback type enumeration.

    - `SELF_ASSESSMENT`: Feedback related to user's self-assessment experience.
    - `COMPARISON`: Feedback related to comparison experience.
    - `OVERALL`: Feedback related to overall app experience.
    - `RESULT`: Feedback related to speech assessment results.
    """

    SELF_ASSESSMENT = "self_assessment"
    COMPARISON = "comparison_ui"
    OVERALL = "overall_experience"
    RESULT = "result"


class FeedbackRequest(BaseModel):
    """Feedback request type.

    Attributes:
        assessment_id: Optional ID of the related assessment, if applicable.
        comment: Optional user comment providing additional feedback details.
        guid: Unique identifier for the user submitting feedback.
        reaction_value: Numerical value representing user's reaction (e.g., rating).
        type: Type of feedback being submitted.
    """

    assessment_id: int | None = Field(default=None, ge=0)
    comment: str | None = None
    guid: UUID
    reaction_value: int = Field(ge=1, le=5)
    type: FeedbackType

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str | None) -> str | None:
        """Validate the comment length if provided."""

        if value is not None and len(value) > 500:
            raise ValueError("comment must not exceed 500 characters.")

        return value
