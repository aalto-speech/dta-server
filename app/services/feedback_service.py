from fastapi.responses import JSONResponse

from app.db import create_assessment_feedback, create_experience_feedback
from app.models.feedback import (
    CreateAssessmentFeedbackInput,
    CreateExperienceFeedbackInput,
    FeedbackClassification,
    FeedbackRequest,
)


def record_feedback(data: FeedbackRequest) -> JSONResponse:
    """Persist feedback based on classification.

    Args:
        data: Feedback payload including classification, score, and optional comment.

    Returns:
        JSONResponse: 201 when feedback is accepted.
    """

    assessment_feedback = {
        FeedbackClassification.SELF_ASSESSMENT,
        FeedbackClassification.RESULT_ACCURACY,
        FeedbackClassification.RESULT_UNDERSTANDING,
    }
    experience_feedback = {
        FeedbackClassification.COMPARISON,
        FeedbackClassification.OVERALL,
    }

    if data.feedback_classification in assessment_feedback:
        if data.assessment_id is None:
            raise ValueError(
                "assessment_id is required for assessment feedback")
        create_assessment_feedback(CreateAssessmentFeedbackInput(
            guid=data.guid,
            assessment_id=data.assessment_id,
            feedback_classification=data.feedback_classification,
            reaction_value=data.reaction_value,
            comment=data.comment,
        ))
    elif data.feedback_classification in experience_feedback:
        create_experience_feedback(CreateExperienceFeedbackInput(
            guid=data.guid,
            feedback_classification=data.feedback_classification,
            reaction_value=data.reaction_value,
            comment=data.comment,
        ))

    return JSONResponse(content={"status": "feedback recorded"}, status_code=201)
