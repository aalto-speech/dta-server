from fastapi.responses import JSONResponse

from app.db import create_assessment_feedback, create_experience_feedback
from app.models.feedback import FeedbackClassification, FeedbackRequest


def record_feedback(data: FeedbackRequest) -> JSONResponse:
    """Persist feedback based on classification."""

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
        create_assessment_feedback(data)
    elif data.feedback_classification in experience_feedback:
        create_experience_feedback(data)

    return JSONResponse(content={"status": "feedback recorded"}, status_code=201)
