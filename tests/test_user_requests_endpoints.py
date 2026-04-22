# pylint: disable=redefined-outer-name

import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.error_handlers import AppError, ErrorType
from app.main import app, delete_users, request_user
from app.models.user_requests import DeleteUserRequest, RequestType, UserDataRequest


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _valid_request_user_form_data(**overrides):
    data = {
        "guid": str(uuid4()),
        "type": "delete",
    }
    data.update(overrides)
    return data


def _valid_delete_users_form_data(**overrides):
    data = {
        "guid": str(uuid4()),
    }
    data.update(overrides)
    return data


def test_request_user_handler_delete_calls_create_user_request(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test handler delete path stores request and returns 202."""

    called = {}
    logged = []

    def _fake_create_user_request(data):
        called["guid"] = str(data.guid)
        called["type"] = str(data.type)

    monkeypatch.setattr("app.services.user_request_service.create_user_request",
                        _fake_create_user_request)
    monkeypatch.setattr(
        "app.services.user_request_service.logger.info",
        lambda message, *args: logged.append((message, args)),
    )
    data = _valid_request_user_form_data()
    request_model = UserDataRequest(
        guid=UUID(data["guid"]),
        type=RequestType(data["type"]),
    )

    response = asyncio.run(request_user(request_model))

    assert response.status_code == 202
    assert response.body == (
        b'{"status":"request_received","message":"Your data deletion request '
        b'has been received and is awaiting admin approval"}'
    )
    assert called == {
        "guid": data["guid"],
        "type": "delete",
    }
    assert logged == [
        ("Stored data deletion request for user %s", (UUID(data["guid"]),)),
    ]


def test_request_user_handler_export_does_not_store_and_returns_501(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test handler export path raises AppError and does not store request."""

    called = {"create_user_request": False}
    logged = []

    def _fake_create_user_request(_):
        called["create_user_request"] = True

    monkeypatch.setattr("app.services.user_request_service.create_user_request",
                        _fake_create_user_request)
    monkeypatch.setattr(
        "app.services.user_request_service.logger.warning",
        lambda message, *args: logged.append((message, args)),
    )
    request_model = UserDataRequest(guid=uuid4(), type=RequestType.EXPORT)

    with pytest.raises(AppError) as exc_info:
        asyncio.run(request_user(request_model))

    assert exc_info.value.status_code == 501
    assert exc_info.value.error_type == ErrorType.NOT_IMPLEMENTED
    assert exc_info.value.message == "Data export requests are not implemented yet."
    assert called["create_user_request"] is False
    assert logged == [
        ("Rejected unsupported data export request for user %s", (request_model.guid,)),
    ]


def test_request_user_endpoint_delete_accepts_valid_payload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test /request/user returns 202 for valid delete request form data."""

    called = {}

    def _fake_create_user_request(data):
        called["guid"] = str(data.guid)
        called["type"] = str(data.type)

    monkeypatch.setattr("app.services.user_request_service.create_user_request",
                        _fake_create_user_request)

    response = client.post(
        "/request/user", data=_valid_request_user_form_data())

    assert response.status_code == 202
    assert response.json()["status"] == "request_received"
    assert called["type"] == "delete"


def test_request_user_endpoint_export_returns_not_implemented(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /request/user returns 501 and does not persist for export request."""

    called = {"create_user_request": False}

    def _fake_create_user_request(_):
        called["create_user_request"] = True

    monkeypatch.setattr("app.services.user_request_service.create_user_request",
                        _fake_create_user_request)

    response = client.post(
        "/request/user",
        data=_valid_request_user_form_data(type="export"),
    )

    assert response.status_code == 501
    assert response.json() == {
        "detail": {
            "type": "NOT_IMPLEMENTED",
            "message": "Data export requests are not implemented yet.",
        },
    }
    assert called["create_user_request"] is False


def test_request_user_endpoint_rejects_invalid_guid(client: TestClient):
    """Test /request/user returns 422 for invalid GUID format."""

    response = client.post(
        "/request/user",
        data=_valid_request_user_form_data(guid="not-a-guid"),
    )

    assert response.status_code == 422


def test_request_user_endpoint_rejects_missing_required_fields(client: TestClient):
    """Test /request/user returns 422 when required fields are missing."""

    data = _valid_request_user_form_data()
    data.pop("type")

    response = client.post("/request/user", data=data)

    assert response.status_code == 422


def test_request_user_endpoint_rejects_invalid_type(client: TestClient):
    """Test /request/user returns 422 when type is outside enum values."""

    response = client.post(
        "/request/user",
        data=_valid_request_user_form_data(type="archive"),
    )

    assert response.status_code == 422


def test_delete_users_handler_calls_delete_user_data(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test handler calls delete_user_data and returns 204 on valid admin access."""

    called = {}
    logged = []

    def _fake_validate_admin_access(_):
        return None

    def _fake_delete_user_data(data):
        called["guid"] = str(data.guid)

    monkeypatch.setattr("app.services.admin_service.auth.validate_admin_access",
                        _fake_validate_admin_access)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data", _fake_delete_user_data)
    monkeypatch.setattr(
        "app.services.admin_service.logger.info",
        lambda message, *args: logged.append((message, args)),
    )

    request_model = DeleteUserRequest(api_key="valid-admin-key", guid=uuid4())
    response = asyncio.run(delete_users(request_model))

    assert response.status_code == 204
    assert called == {
        "guid": str(request_model.guid),
    }
    assert logged == [
        ("Admin deleted all data for user: %s", (request_model.guid,)),
    ]


def test_delete_users_endpoint_accepts_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /users returns 204 for valid admin key and guid."""

    called = {}
    logged = []

    def _fake_validate_admin_access(_):
        return None

    def _fake_delete_user_data(data):
        called["guid"] = str(data.guid)

    monkeypatch.setattr("app.services.admin_service.auth.validate_admin_access",
                        _fake_validate_admin_access)
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
        headers={"X-API-Key": "valid-admin-key"},
        data=payload,
    )

    assert response.status_code == 204
    assert called == {
        "guid": payload["guid"],
    }
    assert logged == [
        ("Admin deleted all data for user: %s", (UUID(payload["guid"]),)),
    ]


def test_delete_users_endpoint_rejects_invalid_api_key(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /users returns 403 for invalid API key."""

    def _fake_validate_admin_access(key: str):
        if key != "valid-admin-key":
            raise AppError(
                status_code=403,
                error_type=ErrorType.INVALID_API_KEY,
                message="Invalid API key",
            )

    monkeypatch.setattr("app.services.admin_service.auth.validate_admin_access",
                        _fake_validate_admin_access)

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-API-Key": "invalid"},
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
    """Test /users returns 422 when X-API-Key header is missing."""

    response = client.request(
        "DELETE", "/users", data=_valid_delete_users_form_data())

    assert response.status_code == 422


def test_delete_users_endpoint_rejects_invalid_guid(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /users returns 422 for invalid GUID format."""

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_admin_access", lambda _key: None)

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-API-Key": "valid-admin-key"},
        data=_valid_delete_users_form_data(guid="not-a-guid"),
    )

    assert response.status_code == 422


def test_delete_users_endpoint_rejects_missing_guid(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /users returns 422 when guid form field is missing."""

    monkeypatch.setattr(
        "app.services.admin_service.auth.validate_admin_access", lambda _key: None)

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-API-Key": "valid-admin-key"},
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
        "app.services.admin_service.auth.validate_admin_access", _deny_admin_access)
    monkeypatch.setattr(
        "app.services.admin_service.delete_user_data", _fake_delete_user_data)

    response = client.request(
        "DELETE",
        "/users",
        headers={"X-API-Key": "invalid"},
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
