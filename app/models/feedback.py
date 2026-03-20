
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class FeedbackType(StrEnum):
    """Feedback type enumeration.

    - `SELF_ASSESSMENT`: Feedback related to user's self-assessment experience.
    - `COMPARISON`: Feedback related to comparison UI experience.
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
        feedback_type: Type of feedback being submitted.
        guid: Unique identifier for the user submitting feedback.
        reaction_value: Numerical value representing user's reaction (e.g., rating).
    """

    assessment_id: int | None
    comment: str | None
    feedback_type: FeedbackType
    guid: UUID
    reaction_value: int
