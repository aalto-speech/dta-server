from uuid import uuid4

import pytest

from app.config import SETTINGS
from app.error_handlers import AppError, ErrorType
from app.validators.auth import validate_admin_access, validate_user_access


def test_validate_admin_access_accepts_matching_api_key() -> None:
    """Admin access should pass when the provided API key matches settings."""

    validate_admin_access(api_key=SETTINGS.admin_api_key)


def test_validate_admin_access_rejects_invalid_api_key() -> None:
    """Admin access should raise a typed error for an invalid API key."""

    with pytest.raises(AppError) as err:
        validate_admin_access(api_key=f"{SETTINGS.admin_api_key}-invalid")

    assert err.value.status_code == 403
    assert err.value.error_type == ErrorType.INVALID_API_KEY
    assert err.value.message == "Invalid API key"


def test_validate_user_access_rejects_unknown_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """User access should fail with USER_NOT_FOUND when the user does not exist."""

    def _fake_get_user(_):
        return None

    monkeypatch.setattr("app.validators.auth.get_user", _fake_get_user)
    monkeypatch.setattr("app.validators.auth.get_user_consent", lambda _: True)

    with pytest.raises(AppError) as err:
        validate_user_access(uuid4())

    assert err.value.status_code == 404
    assert err.value.error_type == ErrorType.USER_NOT_FOUND
    assert err.value.message == "User not found"


def test_validate_user_access_rejects_missing_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    """User access should fail with USER_CONSENT_MISSING when consent is missing."""

    monkeypatch.setattr("app.validators.auth.get_user", lambda _: True)
    monkeypatch.setattr("app.validators.auth.get_user_consent", lambda _: None)

    with pytest.raises(AppError) as err:
        validate_user_access(uuid4())

    assert err.value.status_code == 403
    assert err.value.error_type == ErrorType.USER_CONSENT_MISSING
    assert err.value.message == "User consent missing"


def test_validate_user_access_accepts_existing_user_with_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User access should pass when both user and consent records are present."""

    captured = {"user_guid": None, "consent_guid": None}

    def _fake_get_user(payload):
        captured["user_guid"] = payload.guid
        return True

    def _fake_get_user_consent(payload):
        captured["consent_guid"] = payload.guid
        return True

    guid = uuid4()
    monkeypatch.setattr("app.validators.auth.get_user", _fake_get_user)
    monkeypatch.setattr("app.validators.auth.get_user_consent", _fake_get_user_consent)

    validate_user_access(guid)

    assert captured["user_guid"] == guid
    assert captured["consent_guid"] == guid