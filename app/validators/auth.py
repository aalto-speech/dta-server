from uuid import UUID

from fastapi import Header, HTTPException

from app.config import SETTINGS
from app.db import get_user_consent, get_user_guid


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
    if not get_user_guid(guid) or not get_user_consent(guid):
        raise HTTPException(status_code=403, detail="ASA onboarding required")
