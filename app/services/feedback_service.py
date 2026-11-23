from fastapi.responses import JSONResponse

from app.db import create_feedback, user_owns_assessment
from app.error_handlers import AppError, ErrorType
from app.models.feedback import (
    CreateFeedbackInput,
    FeedbackRequest,
)
from app.utils import rate_limit
from app.utils.logger import get_logger
from app.validators import auth


logger = get_logger(__name__)


def record_feedback(data: FeedbackRequest) -> JSONResponse:
    """Persist feedback based on classification.

    Requires a consented user and, for assessment feedback, that the user owns it.

    Args:
        data: Feedback payload including classification, score, and optional comment.

    Returns:
        JSONResponse: 201 when feedback is accepted.

    Raises:
        AppError: 404 USER_NOT_FOUND, 404 ASSESSMENT_NOT_FOUND, or 429 RATE_LIMITED.
    """

    auth.validate_user_access(data.guid)
    rate_limit.enforce_for_user(rate_limit.FEEDBACK_PER_USER, data.guid)

    if (data.assessment_id is not None
            and not user_owns_assessment(data.guid, data.assessment_id)):
        # Same 404 for missing and not-owned, so assessment ids cannot be probed.
        raise AppError(
            status_code=404,
            error_type=ErrorType.ASSESSMENT_NOT_FOUND,
            message="Assessment not found",
        )

    create_feedback(CreateFeedbackInput(
        guid=data.guid,
        assessment_id=data.assessment_id,
        feedback_classification=data.feedback_classification,
        reaction_value=data.reaction_value,
        comment=data.comment,
    ))

    logger.info("Stored feedback for user %s", data.guid)

    return JSONResponse(content={"status": "feedback recorded"}, status_code=201)
