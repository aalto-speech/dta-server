from uuid import UUID

from fastapi import Header, HTTPException

from app.config import SETTINGS
from app.db import get_user, get_user_consent
from app.models.user_requests import GetUserConsentInput, GetUserInput


def validate_admin_access(api_key: str = Header(...)) -> None:
    """Validate the admin API key for protected endpoints."""

    if api_key != SETTINGS.admin_api_key:
        raise HTTPException(
            status_code=403, detail="Invalid API key")


def validate_user_access(guid: UUID) -> None:
    """Validate that a user has completed onboarding consent."""
    if not get_user(GetUserInput(guid=guid)):
        raise HTTPException(status_code=404, detail="User not found")

    if not get_user_consent(GetUserConsentInput(guid=guid)):
        raise HTTPException(status_code=403, detail="User consent missing")
