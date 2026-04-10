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
    """Feedback request payload.

    Attributes:
        assessment_id: Related assessment ID when applicable.
        comment: Optional user comment.
        guid: The user's GUID.
        reaction_value: Numerical reaction value.
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
        """Validate the comment length when provided."""

        if value is not None and len(value) > FEEDBACK_COMMENT_MAX_LENGTH:
            raise ValueError(
                f"comment must not exceed {FEEDBACK_COMMENT_MAX_LENGTH} characters."
            )

        return value

    @model_validator(mode="after")
    def validate_assessment_id_required_for_assessment_feedback(self):
        """Require assessment_id for assessment-related feedback types."""

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


class CreateAssessmentFeedbackInput(BaseModel):
    """Internal DB input for inserting assessment feedback."""

    guid: UUID
    assessment_id: int = Field(ge=0)
    feedback_classification: FeedbackClassification
    reaction_value: int = Field(ge=1, le=5)
    comment: str | None = None


class CreateExperienceFeedbackInput(BaseModel):
    """Internal DB input for inserting experience feedback."""

    guid: UUID
    feedback_classification: FeedbackClassification
    reaction_value: int = Field(ge=1, le=5)
    comment: str | None = None
