import logging
import sqlite3
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeAlias, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

ErrorHandler: TypeAlias = Callable[[
    Request, Exception], Awaitable[JSONResponse]]


class ErrorType(str, Enum):
    """Canonical error types emitted by backend endpoints."""

    BAD_REQUEST = "BAD_REQUEST"
    INVALID_API_KEY = "INVALID_API_KEY"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    USER_CONSENT_MISSING = "USER_CONSENT_MISSING"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    DATABASE_CONSTRAINT_ERROR = "DATABASE_CONSTRAINT_ERROR"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DATABASE_ERROR = "DATABASE_ERROR"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    HTTP_ERROR = "HTTP_ERROR"


class AppError(Exception):
    """Application exception carrying status code and standardized type."""

    def __init__(self, status_code: int, error_type: ErrorType, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message


def build_error_detail(error_type: ErrorType | str, message: str) -> dict[str, str]:
    """Build the standardized payload returned under the `detail` key."""

    error_type_value = (
        error_type.value if isinstance(
            error_type, ErrorType) else str(error_type)
    )
    return {
        "type": error_type_value,
        "message": message,
    }


def _make_json_error_handler(
    logger: logging.Logger,
    log_message: str,
    error_type: ErrorType,
    detail: str,
    status_code: int,
) -> ErrorHandler:
    """Create a JSON error handler with consistent logging and responses."""

    async def _handler(request: Request, err: Exception) -> JSONResponse:
        logger.exception(
            "%s (%s %s): %s", log_message, request.method, request.url.path, err
        )
        return JSONResponse(
            content={"detail": build_error_detail(error_type, detail)},
            status_code=status_code,
        )

    return _handler


async def _app_error_handler(
    request: Request, err: Exception, logger: logging.Logger
) -> JSONResponse:
    app_err = err if isinstance(err, AppError) else AppError(
        500, ErrorType.INTERNAL_SERVER_ERROR, "Internal server error"
    )

    log_message = "Application error"
    if app_err.status_code >= 500:
        logger.exception(
            "%s (%s %s): %s",
            log_message,
            request.method,
            request.url.path,
            app_err,
        )
    else:
        logger.warning(
            "%s (%s %s): %s",
            log_message,
            request.method,
            request.url.path,
            app_err,
        )

    return JSONResponse(
        content={"detail": build_error_detail(
            app_err.error_type, app_err.message)},
        status_code=app_err.status_code,
    )


async def _http_exception_handler(
    request: Request, err: Exception, logger: logging.Logger
) -> JSONResponse:
    http_err = err if isinstance(err, HTTPException) else HTTPException(
        status_code=500,
        detail="Internal server error",
    )

    if isinstance(http_err.detail, dict) and {"type", "message"}.issubset(http_err.detail):
        detail_dict = cast(dict[str, object], http_err.detail)
        detail = {
            "type": str(detail_dict["type"]),
            "message": str(detail_dict["message"]),
        }
    else:
        detail = build_error_detail(ErrorType.HTTP_ERROR, str(http_err.detail))

    if http_err.status_code >= 500:
        logger.exception(
            "HTTP exception (%s %s): %s",
            request.method,
            request.url.path,
            http_err,
        )
    else:
        logger.warning(
            "HTTP exception (%s %s): %s",
            request.method,
            request.url.path,
            http_err,
        )

    return JSONResponse(
        content={"detail": detail},
        status_code=http_err.status_code,
    )


def register_error_handlers(app: FastAPI, logger: logging.Logger) -> None:
    """Register application-level exception handlers."""

    async def app_error_handler(request: Request, err: Exception) -> JSONResponse:
        return await _app_error_handler(request, err, logger)

    async def http_exception_handler(request: Request, err: Exception) -> JSONResponse:
        return await _http_exception_handler(request, err, logger)

    app.add_exception_handler(
        AppError,
        app_error_handler,
    )

    app.add_exception_handler(
        HTTPException,
        http_exception_handler,
    )

    app.add_exception_handler(
        sqlite3.IntegrityError,
        _make_json_error_handler(
            logger,
            log_message="Integrity error",
            error_type=ErrorType.DATABASE_CONSTRAINT_ERROR,
            detail="Request data violates database constraints",
            status_code=409,
        ),
    )

    app.add_exception_handler(
        sqlite3.OperationalError,
        _make_json_error_handler(
            logger,
            log_message="Operational DB error",
            error_type=ErrorType.DATABASE_UNAVAILABLE,
            detail="Database temporarily unavailable",
            status_code=503,
        ),
    )

    app.add_exception_handler(
        sqlite3.DatabaseError,
        _make_json_error_handler(
            logger,
            log_message="Database error",
            error_type=ErrorType.DATABASE_ERROR,
            detail="Database error",
            status_code=500,
        ),
    )

    app.add_exception_handler(
        Exception,
        _make_json_error_handler(
            logger,
            log_message="Unhandled server error",
            error_type=ErrorType.INTERNAL_SERVER_ERROR,
            detail="Internal server error",
            status_code=500,
        ),
    )
