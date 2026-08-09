import pytest

from app.config import _build_settings


def test_server_delete_key_read_from_new_name(monkeypatch: pytest.MonkeyPatch):
    """SERVER_DELETE_KEY is the name the app is configured with."""

    monkeypatch.setenv("SERVER_DELETE_KEY", "new-name-key")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)

    assert _build_settings().server_delete_key == "new-name-key"


def test_legacy_admin_api_key_still_authorises_deletion(monkeypatch: pytest.MonkeyPatch):
    """A server deployed from a pre-v1.2.0 env file must still be able to erase data.

    Refusing a GDPR deletion because an environment variable was renamed would be a worse
    failure than carrying the old name for a release.
    """

    monkeypatch.delenv("SERVER_DELETE_KEY", raising=False)
    monkeypatch.setenv("ADMIN_API_KEY", "legacy-key")

    assert _build_settings().server_delete_key == "legacy-key"


def test_new_name_wins_when_both_are_set(monkeypatch: pytest.MonkeyPatch):
    """During the transition both may be present; the current name decides."""

    monkeypatch.setenv("SERVER_DELETE_KEY", "new-name-key")
    monkeypatch.setenv("ADMIN_API_KEY", "legacy-key")

    assert _build_settings().server_delete_key == "new-name-key"


def test_production_requires_a_delete_key(monkeypatch: pytest.MonkeyPatch):
    """Production without a delete key means user data cannot be erased on request."""

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SERVER_DELETE_KEY", raising=False)
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SERVER_DELETE_KEY"):
        _build_settings()
