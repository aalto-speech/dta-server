from fastapi import Header, HTTPException

from app.config import SETTINGS


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
