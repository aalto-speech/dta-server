"""Tests for user-owned file storage (recordings)."""
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import SETTINGS
from app.error_handlers import AppError
from app.models.user_requests import DeleteUserRequest
from app.services.admin_service import delete_user
from app.utils.storage import delete_user_audio, user_audio_dir


def _make_recordings(guid, count: int) -> Path:
    """Create `count` fake recordings for a user and return their directory."""

    directory = user_audio_dir(guid)
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (directory / f"{uuid4()}.wav").write_bytes(b"RIFF fake wav %d" % index)
    return directory


def test_user_audio_dir_is_under_the_configured_root():
    """Test a user's directory is a direct child of AUDIO_SAVE_DIR."""

    guid = uuid4()
    directory = user_audio_dir(guid)

    assert directory.parent == Path(SETTINGS.audio_save_dir)
    assert directory.name == str(guid)


def test_delete_user_audio_removes_every_recording():
    """Test deletion removes the user's directory and reports the file count."""

    guid = uuid4()
    directory = _make_recordings(guid, 3)

    removed = delete_user_audio(guid)

    assert removed == 3
    assert not directory.exists()


def test_delete_user_audio_is_a_no_op_without_recordings():
    """Test deletion succeeds for a user who never uploaded audio."""

    assert delete_user_audio(uuid4()) == 0


def test_delete_user_audio_leaves_other_users_untouched():
    """Test deletion is scoped to one user's directory."""

    keep_guid = uuid4()
    keep_dir = _make_recordings(keep_guid, 2)
    delete_guid = uuid4()
    _make_recordings(delete_guid, 1)

    delete_user_audio(delete_guid)

    assert keep_dir.exists()
    assert len(list(keep_dir.glob("*.wav"))) == 2


def test_delete_user_audio_refuses_paths_outside_the_audio_root():
    """Test a GUID that escapes the audio root is rejected before rmtree runs."""

    with pytest.raises(ValueError):
        delete_user_audio("../..")


def test_delete_user_deletes_recordings_and_rows(monkeypatch: pytest.MonkeyPatch):
    """Test the admin endpoint erases the recordings, not just the database rows."""

    guid = uuid4()
    directory = _make_recordings(guid, 2)
    deleted_rows = {}

    monkeypatch.setattr("app.services.admin_service.auth.validate_delete_access",
                        lambda _: None)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data",
        lambda data: deleted_rows.update(guid=str(data.guid)),
    )

    response = delete_user(DeleteUserRequest(delete_key="valid-admin-key", guid=guid))

    assert response.status_code == 204
    assert not directory.exists()
    assert deleted_rows == {"guid": str(guid)}


def test_delete_user_keeps_rows_when_recordings_cannot_be_deleted(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test a failed audio deletion aborts before the row is removed.

    The row is what makes an outstanding deletion request discoverable, so it must
    survive a failure to erase the files.
    """

    guid = uuid4()
    called = {"rows": False}

    def _fail(_):
        raise OSError("permission denied")

    monkeypatch.setattr("app.services.admin_service.auth.validate_delete_access",
                        lambda _: None)
    monkeypatch.setattr("app.services.admin_service.delete_user_audio", _fail)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data",
        lambda _: called.update(rows=True),
    )

    with pytest.raises(AppError) as excinfo:
        delete_user(DeleteUserRequest(delete_key="key", guid=guid))

    assert excinfo.value.status_code == 500
    assert called["rows"] is False
