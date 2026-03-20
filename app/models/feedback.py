
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class FeedbackType(StrEnum):
    """Feedback type enumeration

    Args:
        SELF_ASSESSMENT: Feedback related to user's self-assessment experience
        COMPARISON: Feedback related to comparison UI experience
        OVERALL: Feedback related to overall app experience
        RESULT: Feedback related to speech assessment results
    """

    SELF_ASSESSMENT = "self_assessment"
    COMPARISON = "comparison_ui"
    OVERALL = "overall_experience"
    RESULT = "result"


class FeedbackRequest(BaseModel):
    """Feedback request type"""

    assessment_id: int | None
    comment: str | None
    guid: UUID
    reaction_value: int
    feedback_type: FeedbackType
