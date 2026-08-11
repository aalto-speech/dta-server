from fastapi.responses import JSONResponse

from app.db import set_user_cefr_level
from app.error_handlers import AppError, ErrorType
from app.models.users import (
    SetUserCEFRLevelInput,
    SetUserCEFRLevelRequest,
    SetUserCEFRLevelResponse,
)
from app.utils.logger import get_logger
from app.validators import auth

logger = get_logger(__name__)


def update_user_cefr_level(data: SetUserCEFRLevelRequest) -> JSONResponse:
    """Move the CEFR level a user is working at, from the profile screen.

    The onboarding self-assessment (`users.cefr_level`) is not touched. This writes
    `users.current_cefr_level` and appends to `user_cefr_history`, and cohort ranking in
    /analytics/comparison follows the new value from the next request onwards.

    Args:
        data: Target level and the guid it applies to.

    Returns:
        JSONResponse: 200 with the level as stored.
    """

    auth.validate_user_access(data.guid)

    if not set_user_cefr_level(SetUserCEFRLevelInput(
            guid=data.guid, cefr_level=data.cefr_level)):
        # validate_user_access already 404s for an unknown guid, so reaching here means the
        # row disappeared between the two statements -- a deletion landing mid-request.
        raise AppError(
            status_code=404,
            error_type=ErrorType.USER_NOT_FOUND,
            message="User not found",
        )

    logger.info("User %s moved to CEFR level %s", data.guid, data.cefr_level)

    return JSONResponse(
        content=SetUserCEFRLevelResponse(
            guid=data.guid, cefr_level=data.cefr_level).model_dump(mode="json"),
        status_code=200,
    )
