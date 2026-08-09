from uuid import UUID

from fastapi import Header

from app.config import SETTINGS
from app.db import get_user, get_user_consent
from app.error_handlers import AppError, ErrorType
from app.models.user_requests import GetUserConsentInput, GetUserInput


def validate_delete_access(delete_key: str = Header(...)) -> None:
    """Validate the server delete key that authorises erasing a user's data."""

    if delete_key != SETTINGS.server_delete_key:
        raise AppError(
            status_code=403,
            error_type=ErrorType.INVALID_API_KEY,
            message="Invalid API key",
        )


def validate_client_access(client_key: str | None) -> None:
    """Validate the shared client key (X-Client-Key) when one is configured.

    Disabled when CLIENT_API_KEY is unset, so existing clients keep working. This
    raises the bar against casual scripted abuse; it is not a security boundary --
    the key ships inside the mobile app and is recoverable from a decompiled build.
    """

    if not SETTINGS.client_api_key:
        return

    if client_key != SETTINGS.client_api_key:
        raise AppError(
            status_code=403,
            error_type=ErrorType.INVALID_API_KEY,
            message="Invalid client key",
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
