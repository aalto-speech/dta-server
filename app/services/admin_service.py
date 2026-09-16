
import sqlite3

from fastapi.responses import Response

from app.db import create_user_request, delete_user_data
from app.error_handlers import AppError, ErrorType
from app.models.user_requests import (
    CreateUserRequestInput,
    DeleteUserDataInput,
    DeleteUserRequest,
    RequestStatus,
    RequestType,
)
from app.utils.logger import get_logger
from app.utils.storage import delete_user_audio
from app.validators import auth

logger = get_logger(__name__)


def delete_user(data: DeleteUserRequest) -> Response:
    """Delete all user data after validating admin access, and record that it happened.

    A successful deletion leaves one row in `user_requests` holding the guid, the time and
    the number of recordings removed, and nothing else about the person. That row is the
    only thing that survives them. Data exported before the request was made still sits in
    archive copies the server cannot reach, and this is what tells whoever holds those
    copies which GUIDs in them may no longer be used.

    Args:
        data: Delete payload containing target GUID and admin API key.

    Returns:
        Response: 204 when deletion succeeds.

    Raises:
        AppError: 500 if the recordings cannot be removed, or if the deletion cannot be
            recorded. Either way the user row is left in place, so the deletion stays
            visibly outstanding and can be retried.
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

    # The record of the deletion is written BEFORE the user row is removed, and failing to
    # write it aborts the deletion.
    #
    # These are two transactions, so either can be the last thing that happens. Writing the
    # record second would mean a crash in between leaves a user deleted with nothing saying
    # so -- silent, permanent, and the exact failure this record exists to prevent, because
    # the guid is gone from the database and from the learner's device at the same moment.
    # Writing it first means a crash in between leaves a row claiming a deletion that has
    # not finished. That is a wrong record rather than a missing one, but the user is still
    # present, which makes it findable: export_server_data.sh reports any completed request
    # whose guid is still in `users`, and re-running the deletion clears it. A retry writes
    # a second row rather than updating the first -- two attempts really did happen, and
    # readers of this table care which GUIDs appear in it, not how many times.
    try:
        request_id = create_user_request(CreateUserRequestInput(
            guid=data.guid,
            type=RequestType.DELETE,
            status=RequestStatus.COMPLETED,
            admin_notes=f"{removed} recording(s) removed",
        ))
    except sqlite3.Error as err:
        logger.error(
            "Refusing to delete user %s: the deletion could not be recorded (%s). The "
            "user's recordings are already gone; re-run the deletion to finish it.",
            data.guid,
            err,
        )
        raise AppError(
            status_code=500,
            error_type=ErrorType.INTERNAL_SERVER_ERROR,
            message="Could not record the deletion; the user was not deleted.",
        ) from err

    delete_user_data(DeleteUserDataInput(guid=data.guid))
    # The request id is logged because it outlives everything else here. Once the row is
    # gone the guid resolves to nothing, so the id is the only handle left for tying this
    # line to the record in `user_requests`.
    logger.info(
        "Admin deleted all data for user: %s (%d recording(s) removed, user_requests.id=%s)",
        data.guid,
        removed,
        request_id,
    )
    return Response(status_code=204)
