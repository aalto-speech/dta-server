import hmac
from uuid import UUID

from fastapi import Header

from app.config import SETTINGS
from app.db import get_user, get_user_consent
from app.error_handlers import AppError, ErrorType
from app.models.user_requests import GetUserConsentInput, GetUserInput


def validate_delete_access(delete_key: str = Header(...)) -> None:
    """Validate the server delete key that authorises erasing a user's data.

    An unset SERVER_DELETE_KEY rejects every key, the empty one included, so a
    non-production server without a key cannot be called with an empty header.
    """

    expected = SETTINGS.server_delete_key
    # Bytes, because compare_digest raises TypeError on non-ASCII str
    if not expected or not hmac.compare_digest(delete_key.encode(), expected.encode()):
        raise AppError(
            status_code=403,
            error_type=ErrorType.INVALID_API_KEY,
            message="Invalid API key",
        )


def validate_user_access(guid: UUID) -> None:
    """Validate that a user has completed onboarding consent."""
    if not get_user(GetUserInput(guid=guid)):
        raise AppError(
            status_code=404,
            error_type=ErrorType.USER_NOT_FOUND,
            message="User not found",
        )

    if not get_user_consent(GetUserConsentInput(guid=guid)):
        raise AppError(
            status_code=403,
            error_type=ErrorType.USER_CONSENT_MISSING,
            message="User consent missing",
        )
