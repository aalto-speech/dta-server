# pylint: disable=redefined-outer-name

import asyncio
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
            "Admin deleted all data for user: %s (%d recording(s) removed)",
            (request_model.guid, 0),
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
            "Admin deleted all data for user: %s (%d recording(s) removed)",
            (UUID(payload["guid"]), 0),
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
