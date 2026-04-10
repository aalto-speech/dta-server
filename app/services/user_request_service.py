from fastapi.responses import JSONResponse

from app.db import create_user_request
from app.models.user_requests import (
    CreateUserRequestInput,
    RequestType,
    UserDataRequest,
)


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

    if data.type == RequestType.EXPORT:
        return JSONResponse(
            content={
                "status": "not_implemented",
                "message": "Data export requests are not implemented yet.",
            },
            status_code=501,
        )

    return JSONResponse(
        content={"detail": "Unsupported request type."},
        status_code=400,
    )
