import dataclasses
from uuid import uuid4

import pytest

from app.config import SETTINGS
from app.error_handlers import AppError, ErrorType
from app.validators.auth import validate_delete_access, validate_user_access


DELETE_KEY = "test-delete-key"


def _set_delete_key(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setattr("app.validators.auth.SETTINGS",
                        dataclasses.replace(SETTINGS, server_delete_key=key))


def test_validate_delete_access_accepts_matching_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin access should pass when the provided API key matches settings."""

    _set_delete_key(monkeypatch, DELETE_KEY)
    validate_delete_access(delete_key=DELETE_KEY)


def test_validate_delete_access_rejects_invalid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin access should raise a typed error for an invalid API key."""

    _set_delete_key(monkeypatch, DELETE_KEY)
    with pytest.raises(AppError) as err:
        validate_delete_access(delete_key=f"{DELETE_KEY}-invalid")

    assert err.value.status_code == 403
    assert err.value.error_type == ErrorType.INVALID_API_KEY
    assert err.value.message == "Invalid API key"


@pytest.mark.parametrize("delete_key", ["", "anything"])
def test_validate_delete_access_rejects_all_keys_when_unset(
    monkeypatch: pytest.MonkeyPatch, delete_key: str,
) -> None:
    """With no SERVER_DELETE_KEY configured, no key -- not even an empty one -- passes."""

    _set_delete_key(monkeypatch, "")

    with pytest.raises(AppError) as err:
        validate_delete_access(delete_key=delete_key)

    assert err.value.status_code == 403
    assert err.value.error_type == ErrorType.INVALID_API_KEY


def test_validate_delete_access_rejects_non_ascii_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-ASCII header value is a 403, not a TypeError from compare_digest."""

    _set_delete_key(monkeypatch, DELETE_KEY)
    with pytest.raises(AppError) as err:
        validate_delete_access(delete_key="kéy")

    assert err.value.status_code == 403


def test_validate_user_access_rejects_unknown_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """User access should fail with USER_NOT_FOUND when the user does not exist."""

    monkeypatch.setattr("app.validators.auth.get_user_consent", lambda _: False)

    with pytest.raises(AppError) as err:
        validate_user_access(uuid4())

    assert err.value.status_code == 404
    assert err.value.error_type == ErrorType.USER_NOT_FOUND
    assert err.value.message == "User not found"


def test_validate_user_access_hides_missing_consent_behind_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered user without consent gets the same 404 as an unknown guid."""

    monkeypatch.setattr("app.validators.auth.get_user_consent", lambda _: False)

    with pytest.raises(AppError) as err:
        validate_user_access(uuid4())

    assert err.value.status_code == 404
    assert err.value.error_type == ErrorType.USER_NOT_FOUND


def test_validate_user_access_accepts_existing_user_with_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User access should pass when the user exists and has consented."""

    captured = {"consent_guid": None}

    def _fake_get_user_consent(payload):
        captured["consent_guid"] = payload.guid
        return True

    guid = uuid4()
    monkeypatch.setattr(
        "app.validators.auth.get_user_consent", _fake_get_user_consent)

    validate_user_access(guid)

    assert captured["consent_guid"] == guid
