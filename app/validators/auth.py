from uuid import UUID

from fastapi import Header, HTTPException

from app.config import SETTINGS
from app.db import get_user, get_user_consent


def validate_admin_access(api_key: str = Header(...)) -> None:
    """Validate admin API key for protected endpoints.

    Args:
        api_key: The API key from the X-API-Key header

    Raises:
        HTTPException: If the API key is invalid
    """

    # TODO: Refactor into a helper function.

    if api_key != SETTINGS.admin_api_key:
        raise HTTPException(
            status_code=403, detail="Invalid API key")


def validate_user_access(guid: UUID) -> None:
    """Validate that a user completed onboarding consent.

    A positive consent record implies the user row exists, so a separate
    GUID existence query is not needed.

    Args:
        guid: The user's GUID.

    Raises:
        HTTPException: If onboarding has not been completed.
    """
    if not get_user(guid):
        raise HTTPException(status_code=404, detail="User not found")

    if not get_user_consent(guid):
        raise HTTPException(status_code=403, detail="User consent missing")
