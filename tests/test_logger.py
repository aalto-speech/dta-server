import importlib
import logging
import time
from pathlib import Path
from typing import Generator

import pytest

from app.utils import logger as logger_module


@pytest.fixture(autouse=True)
def isolate_logger_state() -> Generator[None, None, None]:
    """Reset logger bootstrap state around each test."""

    importlib.reload(logger_module)

    app_logger = logging.getLogger("app")
    original_handlers = list(app_logger.handlers)
    original_level = app_logger.level
    original_propagate = app_logger.propagate

    app_logger.handlers.clear()

    try:
        yield
    finally:
        for handler in app_logger.handlers:
            handler.close()

        app_logger.handlers.clear()
        app_logger.handlers.extend(original_handlers)
        app_logger.setLevel(original_level)
        app_logger.propagate = original_propagate


def test_configure_app_logging_bootstraps_file_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configure the app logger once and write the startup banner."""

    monkeypatch.setattr(
        logger_module.time,
        "gmtime",
        lambda *_args, **_kwargs: time.struct_time(
            (2026, 4, 20, 12, 0, 0, 0, 111, 0)
        ),
    )

    logger_module.configure_app_logging(str(tmp_path), "debug")

    app_logger = logging.getLogger("app")
    handlers = [
        handler for handler in app_logger.handlers
        if isinstance(handler, logger_module.UtcDailyFileHandler)
    ]

    assert len(handlers) == 1
    assert app_logger.level == logging.DEBUG
    assert app_logger.propagate is False

    log_path = tmp_path / "2026" / "04" / "2026_04_20.log"
    assert log_path.exists()
    assert "started new log file" in log_path.read_text(encoding="utf-8")


def test_configure_app_logging_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid attaching duplicate file handlers on repeated configuration."""

    monkeypatch.setattr(
        logger_module.time,
        "gmtime",
        lambda *_args, **_kwargs: time.struct_time(
            (2026, 4, 20, 12, 0, 0, 0, 111, 0)
        ),
    )

    logger_module.configure_app_logging(str(tmp_path), logging.INFO)
    logger_module.configure_app_logging(str(tmp_path), logging.ERROR)

    app_logger = logging.getLogger("app")
    handlers = [
        handler for handler in app_logger.handlers
        if isinstance(handler, logger_module.UtcDailyFileHandler)
    ]

    assert len(handlers) == 1
    assert app_logger.level == logging.INFO


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", "app"),
        (".", "app"),
        ("app", "app"),
        ("app.services", "app.services"),
        ("services.feedback_service", "app.services.feedback_service"),
    ],
)
def test_get_logger_normalizes_names(name: str, expected: str) -> None:
    """Return logger names rooted under the shared app namespace."""

    assert logger_module.get_logger(name).name == expected
