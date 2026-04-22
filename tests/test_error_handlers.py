# pylint: disable=redefined-outer-name

import logging
import sqlite3

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.error_handlers import AppError, ErrorType, build_error_detail, register_error_handlers


@pytest.fixture
def app() -> FastAPI:
    """Create an app with routes that exercise all registered error handlers."""

    test_app = FastAPI()
    register_error_handlers(test_app, logging.getLogger("test.error_handlers"))

    @test_app.get("/app-error")
    async def app_error_route() -> None:
        raise AppError(
            status_code=403,
            error_type=ErrorType.INVALID_API_KEY,
            message="Invalid API key",
        )

    @test_app.get("/http-error-string")
    async def http_error_string_route() -> None:
        raise HTTPException(status_code=400, detail="Malformed request")

    @test_app.get("/http-error-typed")
    async def http_error_typed_route() -> None:
        raise HTTPException(
            status_code=422,
            detail={
                "type": "VALIDATION_ERROR",
                "message": "Validation failed",
                "extra": "ignored",
            },
        )

    @test_app.get("/sqlite-integrity")
    async def sqlite_integrity_route() -> None:
        raise sqlite3.IntegrityError("constraint failed")

    @test_app.get("/sqlite-operational")
    async def sqlite_operational_route() -> None:
        raise sqlite3.OperationalError("database is locked")

    @test_app.get("/sqlite-database")
    async def sqlite_database_route() -> None:
        raise sqlite3.DatabaseError("generic database error")

    @test_app.get("/unhandled")
    async def unhandled_route() -> None:
        raise RuntimeError("unexpected failure")

    return test_app


@pytest.fixture
def client(app: FastAPI):
    """Provide a FastAPI test client."""

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_build_error_detail_with_enum_type() -> None:
    """Serialize enum error types into the standard detail payload."""

    detail = build_error_detail(ErrorType.BAD_REQUEST, "Invalid request")

    assert detail == {
        "type": "BAD_REQUEST",
        "message": "Invalid request",
    }


def test_build_error_detail_with_string_type() -> None:
    """Accept custom string error types for detail payloads."""

    detail = build_error_detail("CUSTOM_ERROR", "Something custom happened")

    assert detail == {
        "type": "CUSTOM_ERROR",
        "message": "Something custom happened",
    }


def test_app_error_handler_returns_typed_detail(client: TestClient) -> None:
    """Return standardized typed detail for AppError exceptions."""

    response = client.get("/app-error")

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "type": "INVALID_API_KEY",
            "message": "Invalid API key",
        }
    }


def test_http_exception_with_string_detail_is_normalized(client: TestClient) -> None:
    """Normalize plain HTTPException detail strings into typed payloads."""

    response = client.get("/http-error-string")

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "type": "HTTP_ERROR",
            "message": "Malformed request",
        }
    }


def test_http_exception_with_typed_detail_preserves_type_and_message(
    client: TestClient,
) -> None:
    """Preserve explicit type/message fields from HTTPException detail dictionaries."""

    response = client.get("/http-error-typed")

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "type": "VALIDATION_ERROR",
            "message": "Validation failed",
        }
    }


def test_sqlite_integrity_error_mapping(client: TestClient) -> None:
    """Map sqlite integrity failures to a 409 typed response."""

    response = client.get("/sqlite-integrity")

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "type": "DATABASE_CONSTRAINT_ERROR",
            "message": "Request data violates database constraints",
        }
    }


def test_sqlite_operational_error_mapping(client: TestClient) -> None:
    """Map sqlite operational failures to a 503 typed response."""

    response = client.get("/sqlite-operational")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "type": "DATABASE_UNAVAILABLE",
            "message": "Database temporarily unavailable",
        }
    }


def test_sqlite_database_error_mapping(client: TestClient) -> None:
    """Map sqlite database failures to a 500 typed response."""

    response = client.get("/sqlite-database")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "type": "DATABASE_ERROR",
            "message": "Database error",
        }
    }


def test_unhandled_exception_mapping(client: TestClient) -> None:
    """Map uncaught exceptions to internal server error payloads."""

    response = client.get("/unhandled")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "type": "INTERNAL_SERVER_ERROR",
            "message": "Internal server error",
        }
    }
