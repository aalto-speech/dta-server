from fastapi.responses import JSONResponse

from app.db import create_feedback
from app.models.feedback import (
    CreateFeedbackInput,
    FeedbackRequest,
)
from app.utils.logger import get_logger


logger = get_logger(__name__)


def record_feedback(data: FeedbackRequest) -> JSONResponse:
    """Persist feedback based on classification.

    Args:
        data: Feedback payload including classification, score, and optional comment.

    Returns:
        JSONResponse: 201 when feedback is accepted.
    """

    create_feedback(CreateFeedbackInput(
        guid=data.guid,
        assessment_id=data.assessment_id,
        feedback_classification=data.feedback_classification,
        reaction_value=data.reaction_value,
        comment=data.comment,
    ))

    logger.info("Stored feedback for user %s", data.guid)

    return JSONResponse(content={"status": "feedback recorded"}, status_code=201)
