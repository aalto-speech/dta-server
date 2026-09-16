# pylint: disable=redefined-outer-name

import asyncio
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.error_handlers import AppError, ErrorType
from app.main import app, delete_users
from app.models.user_requests import DeleteUserRequest


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _valid_delete_users_form_data(**overrides):
    data = {
        "guid": str(uuid4()),
    }
    data.update(overrides)
    return data


def test_request_user_route_is_gone(client: TestClient):
    """POST /request/user was removed in v1.3.0; deletion is DELETE /users.

    Asserted rather than assumed: the route carried an `export` type this app must never
    grow, so a reappearance is a regression worth failing the build over.
    """

    response = client.post(
        "/request/user", data={"guid": str(uuid4()), "type": "delete"})

    assert response.status_code == 404
    assert not any(getattr(route, "path", None) == "/request/user"
                   for route in app.routes)


def _record_nothing(recorded: dict):
    """Stand in for create_user_request, capturing the row it would have written."""

    def _record(data):
        recorded.update(
            guid=str(data.guid),
            type=str(data.type),
            status=str(data.status),
            admin_notes=data.admin_notes,
        )
        return 99

    return _record


def test_delete_users_handler_calls_delete_user_data(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test handler calls delete_user_data and returns 204 on valid admin access."""

    called = {}
    logged = []

    def _fake_validate_delete_access(_):
        return None

    def _fake_delete_user_data(data):
        called["guid"] = str(data.guid)

    monkeypatch.setattr("app.services.admin_service.auth.validate_delete_access",
                        _fake_validate_delete_access)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data", _fake_delete_user_data)
    monkeypatch.setattr(
        "app.services.admin_service.create_user_request", _record_nothing({}))
    monkeypatch.setattr(
        "app.services.admin_service.logger.info",
        lambda message, *args: logged.append((message, args)),
    )

    request_model = DeleteUserRequest(delete_key="valid-admin-key", guid=uuid4())
    response = asyncio.run(delete_users(request_model))

    assert response.status_code == 204
    assert called == {
        "guid": str(request_model.guid),
    }
    assert logged == [
        (
            "Admin deleted all data for user: %s "
            "(%d recording(s) removed, user_requests.id=%s)",
            (request_model.guid, 0, 99),
        ),
    ]


def test_delete_users_endpoint_accepts_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /users returns 204 for valid admin key and guid."""

    called = {}
    logged = []

    def _fake_validate_delete_access(_):
        return None

    def _fake_delete_user_data(data):
        called["guid"] = str(data.guid)

    monkeypatch.setattr("app.services.admin_service.auth.validate_delete_access",
                        _fake_validate_delete_access)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data", _fake_delete_user_data)
    monkeypatch.setattr(
        "app.services.admin_service.create_user_request", _record_nothing({}))
    monkeypatch.setattr(
        "app.services.admin_service.logger.info",
        lambda message, *args: logged.append((message, args)),
    )

    payload = _valid_delete_users_form_data()
    response = client.request(
        "DELETE",
        "/users",
        headers={"X-Delete-Key": "valid-admin-key"},
        data=payload,
    )

    assert response.status_code == 204
    assert called == {
        "guid": payload["guid"],
    }
    assert logged == [
        (
            "Admin deleted all data for user: %s "
            "(%d recording(s) removed, user_requests.id=%s)",
            (UUID(payload["guid"]), 0, 99),
        ),
    ]


def test_delete_users_endpoint_rejects_invalid_api_key(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /users returns 403 for invalid API key."""

    def _fake_validate_delete_access(key: str):
        if key != "valid-admin-key":
            raise AppError(
                status_code=403,
                error_type=ErrorType.INVALID_API_KEY,
                message="Invalid API key",
            )

    monkeypatch.setattr("app.services.admin_service.auth.validate_delete_access",
                        _fake_validate_delete_access)

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-Delete-Key": "invalid"},
        data=_valid_delete_users_form_data(),
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "type": "INVALID_API_KEY",
            "message": "Invalid API key",
        }
    }


def test_delete_users_endpoint_rejects_missing_api_key_header(client: TestClient):
    """Test /users returns 422 when X-Delete-Key header is missing."""

    response = client.request(
        "DELETE", "/users", data=_valid_delete_users_form_data())

    assert response.status_code == 422


def test_delete_users_endpoint_rejects_invalid_guid(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /users returns 422 for invalid GUID format."""

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_delete_access", lambda _key: None)

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-Delete-Key": "valid-admin-key"},
        data=_valid_delete_users_form_data(guid="not-a-guid"),
    )

    assert response.status_code == 422


def test_delete_users_endpoint_rejects_missing_guid(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /users returns 422 when guid form field is missing."""

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_delete_access", lambda _key: None)

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-Delete-Key": "valid-admin-key"},
        data={},
    )

    assert response.status_code == 422


def test_delete_users_auth_failure_short_circuits_before_delete(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test auth failure prevents delete_user_data execution."""

    called = {"delete_user_data": False}

    def _deny_admin_access(_):
        raise AppError(
            status_code=403,
            error_type=ErrorType.INVALID_API_KEY,
            message="Invalid API key",
        )

    def _fake_delete_user_data(_):
        called["delete_user_data"] = True

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_delete_access", _deny_admin_access)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data", _fake_delete_user_data)

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-Delete-Key": "invalid"},
        data=_valid_delete_users_form_data(),
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "type": "INVALID_API_KEY",
            "message": "Invalid API key",
        }
    }
    assert called["delete_user_data"] is False


def test_failed_recording_delete_is_recorded_and_leaves_the_user_row(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """A failed erase is visible in the database, not only in the log.

    This audit trail used to live in POST /request/user. That route is gone, so it moved
    here -- a deletion that fails leaves data the user believes was erased, and the guid
    that could find it again has just been wiped from their device.
    """

    recorded = {}
    called = {"delete_user_data": False}

    def _fail(_guid):
        raise OSError("disk on fire")

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_delete_access", lambda _key: None)
    monkeypatch.setattr("app.services.admin_service.delete_user_audio", _fail)
    monkeypatch.setattr(
        "app.services.admin_service.create_user_request",
        lambda data: recorded.update(guid=str(data.guid), type=str(data.type)),
    )
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data",
        lambda _data: called.update(delete_user_data=True),
    )

    payload = _valid_delete_users_form_data()
    response = client.request(
        "DELETE",
        "/users",
        headers={"X-Delete-Key": "valid-admin-key"},
        data=payload,
    )

    assert response.status_code == 500
    assert recorded == {"guid": payload["guid"], "type": "delete"}
    # Recordings are deleted before rows, so the user row survives a failure and the
    # outstanding deletion stays discoverable.
    assert called["delete_user_data"] is False


def test_successful_deletion_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """A deletion that WORKS must leave a row behind. The whole point of the table.

    Until v1.4.0 only a failed deletion was recorded, so the database could not answer
    "whose data is void?" -- the only trace of a completed erase was an INFO log line, and
    the row it referred to was gone. Data exported before the request still sits in archive
    copies the server cannot reach, and this row is what identifies it there.
    """

    recorded = {}

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_delete_access", lambda _key: None)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_audio", lambda _guid: 3)
    monkeypatch.setattr(
        "app.services.admin_service.create_user_request", _record_nothing(recorded))
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data", lambda _data: None)

    payload = _valid_delete_users_form_data()
    response = client.request(
        "DELETE",
        "/users",
        headers={"X-Delete-Key": "valid-admin-key"},
        data=payload,
    )

    assert response.status_code == 204
    assert recorded == {
        "guid": payload["guid"],
        "type": "delete",
        "status": "completed",
        # The recording count travels with the row: it is what tells whoever holds an
        # archive copy whether audio was part of what is now void.
        "admin_notes": "3 recording(s) removed",
    }


def test_deletion_is_abandoned_when_it_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """If the record cannot be written, the user row stays and the deletion is retried.

    The alternative -- delete anyway -- produces the one outcome nothing can recover from:
    a user erased with nothing anywhere saying it happened, no guid left to look them up
    by, and archive copies that can never be reconciled. Leaving the row costs a retry.
    """

    called = {"delete_user_data": False}

    def _cannot_record(_data):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_delete_access", lambda _key: None)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_audio", lambda _guid: 1)
    monkeypatch.setattr(
        "app.services.admin_service.create_user_request", _cannot_record)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data",
        lambda _data: called.update(delete_user_data=True),
    )

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-Delete-Key": "valid-admin-key"},
        data=_valid_delete_users_form_data(),
    )

    assert response.status_code == 500
    assert called["delete_user_data"] is False


def test_the_recorded_deletion_survives_the_user_row(tmp_path: Path):
    """End to end against a real database: the row outlives what it describes.

    The service tests above stub the database out, so this is what actually proves the
    cascade is gone -- under the v1.3.0 schema this row vanished with the user.
    """

    db_path = tmp_path / "dta.db"
    db = sqlite3.connect(db_path)
    db.executescript(
        (Path(__file__).resolve().parents[1] / "app" / "schema.sql")
        .read_text(encoding="utf-8"))
    db.execute("PRAGMA foreign_keys = ON")

    guid = str(uuid4())
    db.execute(
        "INSERT INTO users (guid, consent_accepted, consent_timestamp, cefr_level) "
        "VALUES (?, 1, '2026-01-01T00:00:00Z', 'A2')", (guid,))
    db.execute(
        "INSERT INTO user_requests (guid, type, status, processed_at) "
        "VALUES (?, 'delete', 'completed', CURRENT_TIMESTAMP)", (guid,))
    db.execute("DELETE FROM users WHERE guid = ?", (guid,))
    db.commit()

    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert db.execute(
        "SELECT guid, status FROM user_requests").fetchall() == [(guid, "completed")]


def test_rejected_delete_is_logged_with_the_guid(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """A 403 must leave a record of WHO asked to be forgotten.

    The key is checked before the guid is used anywhere else, so without this the one case
    where a deletion silently did not happen is also the case that leaves no trace of whose
    data it was -- the client wipes its local copy either way, so the guid is gone from the
    device too.
    """

    logged = []

    def _deny(_key):
        raise AppError(
            status_code=403,
            error_type=ErrorType.INVALID_API_KEY,
            message="Invalid API key",
        )

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_delete_access", _deny)
    monkeypatch.setattr(
        "app.services.admin_service.logger.warning",
        lambda message, *args: logged.append((message, args)),
    )

    payload = _valid_delete_users_form_data()
    response = client.request(
        "DELETE", "/users",
        headers={"X-Delete-Key": "wrong"}, data=payload)

    assert response.status_code == 403
    assert logged, "a rejected deletion was not logged"
    assert str(UUID(payload["guid"])) in str(logged[0][1])
