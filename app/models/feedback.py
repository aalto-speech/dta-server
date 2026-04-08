from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

FEEDBACK_COMMENT_MAX_LENGTH = 500


class FeedbackClassification(StrEnum):
    """Feedback type enumeration.

    - `SELF_ASSESSMENT`: Feedback related to user's self-assessment experience.
    - `COMPARISON`: Feedback related to comparison experience.
    - `OVERALL`: Feedback related to overall app experience.
    - `RESULT_ACCURACY`: Feedback related to accuracy of assessment results.
    - `RESULT_UNDERSTANDING`: Feedback related to user's understanding of assessment results.
    """

    SELF_ASSESSMENT = "self_assessment"
    COMPARISON = "comparison_ui"
    OVERALL = "overall_experience"
    RESULT_ACCURACY = "result_accuracy"
    RESULT_UNDERSTANDING = "result_understanding"


class FeedbackRequest(BaseModel):
    """Feedback request type.

    Attributes:
        assessment_id: Optional ID of the related assessment, if applicable.
        comment: Optional user comment providing additional feedback details.
        guid: Unique identifier for the user submitting feedback.
        reaction_value: Numerical value representing user's reaction (e.g., rating).
        feedback_classification: Type of feedback being submitted.
    """

    assessment_id: int | None = Field(default=None, ge=0)
    comment: str | None = None
    guid: UUID
    reaction_value: int = Field(ge=1, le=5)
    feedback_classification: FeedbackClassification

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str | None) -> str | None:
        """Validate the comment length if provided."""

        if value is not None and len(value) > FEEDBACK_COMMENT_MAX_LENGTH:
            raise ValueError(
                f"comment must not exceed {FEEDBACK_COMMENT_MAX_LENGTH} characters."
            )

        return value

    @model_validator(mode="after")
    def validate_assessment_id_required_for_assessment_feedback(self):
        """Validate that assessment_id is provided for assessment-related feedback types."""

        assessment_feedback_types = {
            FeedbackClassification.SELF_ASSESSMENT,
            FeedbackClassification.RESULT_ACCURACY,
            FeedbackClassification.RESULT_UNDERSTANDING,
        }

        if self.feedback_classification in assessment_feedback_types:
            if self.assessment_id is None:
                raise ValueError(
                    f"assessment_id is required for feedback type '{self.feedback_classification}'."
                )

        return self
