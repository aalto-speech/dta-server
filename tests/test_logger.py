import importlib
import io
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

    log_path = tmp_path / "2026" / "04" / "2026-04-20.log"
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


def test_utc_daily_file_handler_rollover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that log file rolls over and new file is created on UTC date change."""
    # Simulate two consecutive days
    day1 = time.struct_time((2026, 4, 20, 23, 59, 59, 0, 111, 0))
    day2 = time.struct_time((2026, 4, 21, 0, 0, 1, 1, 112, 0))

    # Mutable time pointer
    current_time = {"now": day1}

    def fake_gmtime(*_args, **_kwargs):
        return current_time["now"]

    monkeypatch.setattr(logger_module.time, "gmtime", fake_gmtime)

    logger_module.configure_app_logging(str(tmp_path), "INFO")
    app_logger = logging.getLogger("app")

    # Log something on day 1
    app_logger.info("log entry on day 1")

    log_path1 = tmp_path / "2026" / "04" / "2026-04-20.log"
    assert log_path1.exists()
    content1 = log_path1.read_text(encoding="utf-8")
    assert "log entry on day 1" in content1

    # Move to day 2 and log again
    current_time["now"] = day2
    app_logger.info("log entry on day 2")

    log_path2 = tmp_path / "2026" / "04" / "2026-04-21.log"
    assert log_path2.exists()
    content2 = log_path2.read_text(encoding="utf-8")
    assert "log entry on day 2" in content2
    # Check rollover message in new file
    assert "rolling over to a new log file" in content2 or "started new log file" in content2


def test_startup_banner_resumes_existing_log_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup banner should announce resumed state when the daily file already has content."""

    monkeypatch.setattr(
        logger_module.time,
        "gmtime",
        lambda *_args, **_kwargs: time.struct_time(
            (2026, 4, 20, 12, 0, 0, 0, 111, 0)),
    )

    existing_log_path = tmp_path / "2026" / "04" / "2026-04-20.log"
    existing_log_path.parent.mkdir(parents=True, exist_ok=True)
    existing_log_path.write_text("already-had-content\n", encoding="utf-8")

    logger_module.configure_app_logging(str(tmp_path), "info")

    content = existing_log_path.read_text(encoding="utf-8")
    assert "resumed existing log file after service restart" in content


def test_handler_emit_handles_file_write_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emit should route write failures through the fallback exception handler."""

    handler = logger_module.UtcDailyFileHandler(str(tmp_path))
    record = logging.LogRecord(
        "app", logging.INFO, __file__, 0, "msg", (), None)
    captured = {}

    def _raise_os_error(_handler, _record):
        raise OSError("disk error")

    def _capture(_record, exc):
        captured["exc"] = exc

    monkeypatch.setattr(logging.FileHandler, "emit", _raise_os_error)
    monkeypatch.setattr(handler, "_handle_emit_exception", _capture)

    handler.emit(record)

    assert isinstance(captured["exc"], OSError)
    handler.close()


def test_rollover_returns_early_when_day_has_not_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollover should no-op when UTC day key remains the same."""

    day = time.struct_time((2026, 4, 20, 12, 0, 0, 0, 111, 0))
    monkeypatch.setattr(logger_module.time, "gmtime",
                        lambda *_args, **_kwargs: day)

    handler = logger_module.UtcDailyFileHandler(str(tmp_path))
    original_path = handler._current_log_path

    handler._rollover_if_needed()

    assert handler._current_log_path == original_path
    handler.close()


def test_emit_structured_event_opens_stream_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured events should open a stream when it is currently unset."""

    handler = logger_module.UtcDailyFileHandler(str(tmp_path))
    opened_stream = io.StringIO()
    calls = []

    handler.stream.close()
    handler.stream = None

    monkeypatch.setattr(handler, "_open", lambda: opened_stream)
    monkeypatch.setattr(
        logging.FileHandler,
        "emit",
        lambda _handler, _record: calls.append("emitted"),
    )

    handler._emit_structured_event(logging.INFO, "hello")

    assert handler.stream is opened_stream
    assert calls == ["emitted"]
    handler.close()


def test_emit_structured_event_handles_write_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured event emission should trigger exception fallback on write errors."""

    handler = logger_module.UtcDailyFileHandler(str(tmp_path))
    captured = {}

    def _raise_type_error(_handler, _record):
        raise TypeError("bad write")

    def _capture(_record, exc):
        captured["exc"] = exc

    monkeypatch.setattr(logging.FileHandler, "emit", _raise_type_error)
    monkeypatch.setattr(handler, "_handle_emit_exception", _capture)

    handler._emit_structured_event(logging.INFO, "hello")

    assert isinstance(captured["exc"], TypeError)
    handler.close()


def test_handle_emit_exception_reports_and_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_handle_emit_exception should call handleError and fallback writer."""

    handler = logger_module.UtcDailyFileHandler(str(tmp_path))
    record = logging.LogRecord(
        "app", logging.ERROR, __file__, 0, "msg", (), None)
    exc = ValueError("oops")
    calls = []

    monkeypatch.setattr(handler, "handleError",
                        lambda rec: calls.append(("handle", rec)))
    monkeypatch.setattr(
        handler,
        "_emit_fallback_error",
        lambda rec, err: calls.append(("fallback", rec, err)),
    )

    handler._handle_emit_exception(record, exc)

    assert calls[0] == ("handle", record)
    assert calls[1] == ("fallback", record, exc)
    handler.close()


def test_emit_fallback_error_handles_stderr_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback logger should swallow stderr write errors."""

    class BrokenStderr:
        """Minimal stderr double that always fails writes."""

        def write(self, _text: str) -> int:
            raise UnicodeError("cannot write")

    handler = logger_module.UtcDailyFileHandler(str(tmp_path))
    record = logging.LogRecord(
        "app.test", logging.WARNING, __file__, 0, "msg", (), None)

    monkeypatch.setattr(logger_module.sys, "stderr", BrokenStderr())

    # Should not raise despite stderr write failing.
    handler._emit_fallback_error(record, OSError("disk full"))
    handler.close()


def test_configure_app_logging_returns_on_second_event_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second event check inside lock should short-circuit configuration."""

    class _LockThatSetsConfigured:
        def __enter__(self):
            logger_module._LOGGING_CONFIGURED.set()
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    logger_module._LOGGING_CONFIGURED.clear()
    monkeypatch.setattr(logger_module, "_LOGGING_CONFIG_LOCK",
                        _LockThatSetsConfigured())

    logger_module.configure_app_logging(str(tmp_path), logging.INFO)

    app_logger = logging.getLogger("app")
    assert not any(
        isinstance(handler, logger_module.UtcDailyFileHandler)
        for handler in app_logger.handlers
    )


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("   ", logging.INFO),
        ("invalid", logging.INFO),
        ("debug", logging.DEBUG),
        (logging.ERROR, logging.ERROR),
    ],
)
def test_resolve_log_level_handles_empty_invalid_and_valid_values(level, expected: int) -> None:
    """Resolve log level should default for empty/invalid values and accept valid ones."""

    assert logger_module._resolve_log_level(level) == expected


def test_safe_relative_log_path_falls_back_for_outside_paths(tmp_path: Path) -> None:
    """Safe relative path helper should return absolute string when path is outside root."""

    outside = Path("/tmp/not-under-root.log")

    result = logger_module._safe_relative_log_path(tmp_path, outside)

    assert result == str(outside)
