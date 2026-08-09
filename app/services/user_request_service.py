from fastapi.responses import JSONResponse

from app.db import create_user_request, delete_user_data
from app.error_handlers import AppError, ErrorType
from app.models.user_requests import (
    CreateUserRequestInput,
    DeleteUserDataInput,
    RequestType,
    UserDataRequest,
)
from app.utils.logger import get_logger
from app.utils.storage import delete_user_audio

logger = get_logger(__name__)


def _delete_user_now(data: UserDataRequest) -> str:
    """Delete a user's recordings and rows immediately; report the outcome.

    Returns "deleted" when nothing of the user remains (including when there was
    nothing to delete -- retries and unknown guids are idempotent), or "pending"
    when something failed and is logged for a maintainer.

    Every attempt is logged with the guid. This is the audit trail the client asked
    for: the app wipes its local copy either way, so a deletion that fails silently
    leaves data behind that the user believes is gone -- and the guid that could
    find it again has just been thrown away from the device.
    """

    try:
        # Recordings first, same order and reasoning as the admin path
        # (app/services/admin_service.py): failing here leaves the user row in
        # place, so the failure stays discoverable and a retry converges.
        removed = delete_user_audio(data.guid)
        delete_user_data(DeleteUserDataInput(guid=data.guid))
        logger.info(
            "User-requested deletion COMPLETED for guid=%s (%d recording(s) removed)",
            data.guid,
            removed,
        )
        return "deleted"
    except Exception as err:  # pylint: disable=broad-exception-caught
        # Deliberately broad: the contract with the client is "any 2xx means the
        # request arrived, stop retrying" -- an exception escaping here would turn
        # into a 5xx and make the app retry a request we already hold.
        logger.error(
            "User-requested deletion FAILED for guid=%s: %s -- data may remain, "
            "manual cleanup required",
            data.guid,
            err,
        )
        try:
            # Also visible in the database for maintainers (the user row still
            # exists, because recordings are deleted before rows).
            create_user_request(CreateUserRequestInput(
                guid=data.guid, type=RequestType.DELETE))
        except Exception as record_err:  # pylint: disable=broad-exception-caught
            logger.error(
                "Could not record failed deletion for guid=%s: %s -- the log line "
                "above is the only trace",
                data.guid,
                record_err,
            )
        return "pending"


def handle_user_request(data: UserDataRequest) -> JSONResponse:
    """Handle user delete and export requests.

    Args:
        data: User request payload with GUID and request type.

    Returns:
        JSONResponse: 202 for delete requests (with the outcome in the body),
        501 for export requests.
    """

    if data.type == RequestType.DELETE:
        # Deletion happens now, not "awaiting admin approval": the user was told
        # their data is being removed, so it is. Always 202 -- the outcome rides in
        # the body ("deleted" or "pending") and never changes the client's retry
        # logic, which keys off the HTTP status alone.
        outcome = _delete_user_now(data)
        message = (
            "Your data has been deleted."
            if outcome == "deleted"
            else "Your request has been received; removal is underway."
        )
        return JSONResponse(
            content={"status": outcome, "message": message},
            status_code=202,
        )

    logger.warning(
        "Rejected unsupported data export request for user %s", data.guid)
    raise AppError(
        status_code=501,
        error_type=ErrorType.NOT_IMPLEMENTED,
        message="Data export requests are not implemented yet.",
    )
