from fastapi.responses import JSONResponse

from app.db import create_user_request
from app.error_handlers import AppError, ErrorType
from app.models.user_requests import (
    CreateUserRequestInput,
    RequestType,
    UserDataRequest,
)
from app.utils.logger import get_logger


logger = get_logger(__name__)


def handle_user_request(data: UserDataRequest) -> JSONResponse:
    """Handle user delete and export requests.

    Args:
        data: User request payload with GUID and request type.

    Returns:
        JSONResponse: 202 for delete requests, 501 for export requests.
    """

    if data.type == RequestType.DELETE:
        create_user_request(CreateUserRequestInput(
            guid=data.guid, type=data.type))
        logger.info("Stored data deletion request for user %s", data.guid)
        return JSONResponse(
            content={
                "status": "request_received",
                "message": (
                    "Your data deletion request has been received "
                    + "and is awaiting admin approval"
                ),
            },
            status_code=202,
        )

    logger.warning(
        "Rejected unsupported data export request for user %s", data.guid)
    raise AppError(
        status_code=501,
        error_type=ErrorType.NOT_IMPLEMENTED,
        message="Data export requests are not implemented yet.",
    )
