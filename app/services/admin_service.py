
from fastapi.responses import Response

from app.db import create_user_request, delete_user_data
from app.error_handlers import AppError, ErrorType
from app.models.user_requests import (
    CreateUserRequestInput,
    DeleteUserDataInput,
    DeleteUserRequest,
    RequestType,
)
from app.utils.logger import get_logger
from app.utils.storage import delete_user_audio
from app.validators import auth

logger = get_logger(__name__)


def delete_user(data: DeleteUserRequest) -> Response:
    """Delete all user data after validating admin access.

    Args:
        data: Delete payload containing target GUID and admin API key.

    Returns:
        Response: 204 when deletion succeeds.

    Raises:
        AppError: 500 if the recordings cannot be removed; the user row is left in
            place so the deletion stays visibly outstanding and can be retried.
    """

    try:
        auth.validate_delete_access(data.delete_key)
    except AppError:
        # The guid is the point of this log line. A 403 is rejected before the guid is used
        # anywhere else, so without it the one case where someone asked to be forgotten and
        # was not is also the case that leaves no trace of who they were -- the client wipes
        # its local copy either way, so the guid is gone from the device too.
        #
        # A wrong key is a misconfigured build, not a user action: it cannot be retried into
        # success, and it fails for everyone at once. WARNING rather than INFO so it stands
        # out if it ever starts happening.
        logger.warning(
            "DELETION REJECTED for guid=%s: the delete key did not match. This cannot "
            "succeed on retry -- check the client build's key against SERVER_DELETE_KEY.",
            data.guid,
        )
        raise

    # Recordings go first, deliberately. Deleting the row first and then failing on
    # the files would leave audio nothing in the database points at any more --
    # undiscoverable data for a user who asked to be forgotten. This order fails
    # with the user still present, and a retry converges (both steps are idempotent).
    try:
        removed = delete_user_audio(data.guid)
    except (OSError, ValueError) as err:
        logger.error("Failed to delete recordings for user %s: %s", data.guid, err)
        # Also visible in the database, not only in the log. The user row still exists
        # (recordings are deleted first), so an outstanding deletion stays discoverable by
        # a maintainer even if nobody is reading logs that day. Inherited from the removed
        # POST /request/user path, which is where this audit trail used to live.
        try:
            create_user_request(CreateUserRequestInput(
                guid=data.guid, type=RequestType.DELETE))
        except Exception as record_err:  # pylint: disable=broad-exception-caught
            logger.error(
                "Could not record the failed deletion for guid=%s: %s -- the log line "
                "above is the only trace",
                data.guid,
                record_err,
            )
        raise AppError(
            status_code=500,
            error_type=ErrorType.INTERNAL_SERVER_ERROR,
            message="Could not delete the user's recordings; no data was deleted.",
        ) from err

    delete_user_data(DeleteUserDataInput(guid=data.guid))
    logger.info(
        "Admin deleted all data for user: %s (%d recording(s) removed)",
        data.guid,
        removed,
    )
    return Response(status_code=204)
