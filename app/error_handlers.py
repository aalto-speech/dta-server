import logging
import sqlite3
from collections.abc import Awaitable, Callable
from typing import TypeAlias

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ErrorHandler: TypeAlias = Callable[[
    Request, Exception], Awaitable[JSONResponse]]


def _make_json_error_handler(
    logger: logging.Logger,
    log_message: str,
    detail: str,
    status_code: int,
) -> ErrorHandler:
    """Factory to create JSON error handlers with consistent logging and response structure."""

    async def _handler(request: Request, err: Exception) -> JSONResponse:
        logger.exception(
            "%s (%s %s): %s", log_message, request.method, request.url.path, err
        )
        return JSONResponse(content={"detail": detail}, status_code=status_code)

    return _handler


def register_error_handlers(app: FastAPI, logger: logging.Logger) -> None:
    """Register application-level exception handlers."""

    app.add_exception_handler(
        sqlite3.IntegrityError,
        _make_json_error_handler(
            logger,
            log_message="Integrity error",
            detail="Request data violates database constraints",
            status_code=409,
        ),
    )

    app.add_exception_handler(
        sqlite3.OperationalError,
        _make_json_error_handler(
            logger,
            log_message="Operational DB error",
            detail="Database temporarily unavailable",
            status_code=503,
        ),
    )

    app.add_exception_handler(
        sqlite3.DatabaseError,
        _make_json_error_handler(
            logger,
            log_message="Database error",
            detail="Database error",
            status_code=500,
        ),
    )

    app.add_exception_handler(
        Exception,
        _make_json_error_handler(
            logger,
            log_message="Unhandled server error",
            detail="Internal server error",
            status_code=500,
        ),
    )
