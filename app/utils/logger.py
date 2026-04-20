import logging
from pathlib import Path
import sys
import threading
import time
from contextlib import contextmanager
import fcntl


_APP_LOGGER_NAME = "app"
_DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_LOGGING_CONFIG_LOCK = threading.Lock()
_LOGGING_CONFIGURED = threading.Event()
_LOG_WRITE_EXCEPTIONS = (OSError, TypeError, UnicodeError, ValueError)


class UtcDailyFileHandler(logging.FileHandler):
    """File handler that rolls over automatically when the UTC date changes."""

    def __init__(self, logs_root: str, encoding: str = "utf-8") -> None:
        now_utc = time.gmtime()
        self._logs_root = Path(logs_root)
        self._interprocess_lock_path = self._logs_root / ".app-logger.lock"
        self._current_day = _utc_day_key(now_utc)
        self._current_log_path = _create_logs_path_for_time(
            self._logs_root, now_utc)
        self._had_existing_content = self._current_log_path.exists(
        ) and self._current_log_path.stat().st_size > 0

        super().__init__(self._current_log_path, mode="a", encoding=encoding)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if _utc_day_key(time.gmtime()) != self._current_day:
                self._rollover_if_needed()

            logging.FileHandler.emit(self, record)
        except _LOG_WRITE_EXCEPTIONS as exc:
            self._handle_emit_exception(record, exc)

    def emit_startup_banner(self) -> None:
        """Write the startup banner for the current log file."""

        if self._had_existing_content:
            message = "resumed existing log file after service restart"
        else:
            message = "started new log file"

        self._emit_structured_event(logging.INFO, message)

    def _rollover_if_needed(self) -> None:
        self.acquire()
        try:
            with self._interprocess_lock():
                now_utc = time.gmtime()
                next_day = _utc_day_key(now_utc)
                if next_day == self._current_day:
                    return

                previous_log_path = self._current_log_path
                next_path = _create_logs_path_for_time(
                    self._logs_root, now_utc)
                next_path_existed = next_path.exists()
                if self.stream is not None:
                    self.stream.close()

                self.baseFilename = str(next_path)
                self.stream = self._open()
                self._current_day = next_day
                self._current_log_path = next_path
                self._had_existing_content = next_path.exists() and next_path.stat().st_size > 0

                # Emit rollover metadata only when this process created the new day's file.
                if not next_path_existed:
                    self._emit_structured_event(
                        logging.INFO,
                        "rolling over to a new log file; "
                        f"previous logfile: {_safe_relative_log_path(self._logs_root, previous_log_path)}",
                    )
        finally:
            self.release()

    def _emit_structured_event(self, level: int, message: str) -> None:
        record = logging.LogRecord(
            name=_APP_LOGGER_NAME,
            level=level,
            pathname=__file__,
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )

        try:
            if self.stream is None:
                self.stream = self._open()

            logging.FileHandler.emit(self, record)
        except _LOG_WRITE_EXCEPTIONS as exc:
            self._handle_emit_exception(record, exc)

    @contextmanager
    def _interprocess_lock(self):
        lock_file = None
        try:
            self._logs_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock_file = open(self._interprocess_lock_path,
                             "a", encoding="utf-8")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if lock_file is not None:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_file.close()

    def _handle_emit_exception(self, record: logging.LogRecord, exc: Exception) -> None:
        self.handleError(record)
        self._emit_fallback_error(record, exc)

    def _emit_fallback_error(self, record: logging.LogRecord, exc: Exception) -> None:
        fallback_message = (
            "[app-logger] failed to write log record "
            f"(logger={record.name}, level={record.levelname}): {exc}\n"
        )
        try:
            sys.stderr.write(fallback_message)
        except _LOG_WRITE_EXCEPTIONS:
            pass


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger.

    Logging handlers are configured by calling ``configure_app_logging`` during
    app startup.
    """

    normalized_name = _normalize_logger_name(name)
    return logging.getLogger(normalized_name)


def configure_app_logging(logs_root: str, level: int | str = logging.INFO) -> None:
    """Configure the shared app logger once for the current process."""

    if _LOGGING_CONFIGURED.is_set():
        return

    with _LOGGING_CONFIG_LOCK:
        if _LOGGING_CONFIGURED.is_set():
            return

        app_logger = logging.getLogger(_APP_LOGGER_NAME)

        if not any(isinstance(handler, UtcDailyFileHandler) for handler in app_logger.handlers):
            file_handler = UtcDailyFileHandler(
                logs_root, encoding="utf-8")
            formatter = logging.Formatter(
                _DEFAULT_LOG_FORMAT
            )
            formatter.converter = time.gmtime
            file_handler.setFormatter(formatter)
            app_logger.addHandler(file_handler)
            file_handler.emit_startup_banner()

        app_logger.setLevel(_resolve_log_level(level))
        app_logger.propagate = False
        _LOGGING_CONFIGURED.set()


def _resolve_log_level(level: int | str) -> int:
    if isinstance(level, int):
        return level

    normalized = level.strip().upper() if isinstance(level, str) else ""
    if not normalized:
        return logging.INFO

    resolved = logging.getLevelNamesMapping().get(normalized)
    return resolved if isinstance(resolved, int) else logging.INFO


def _create_logs_path_for_time(logs_root: Path, now_utc: time.struct_time) -> Path:
    year = time.strftime("%Y", now_utc)
    month = time.strftime("%m", now_utc)
    year_month_day = time.strftime("%Y_%m_%d", now_utc)
    log_filename = f"{year_month_day}.log"

    output_dir = logs_root / year / month
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    logs_path = output_dir / log_filename
    return logs_path


def _utc_day_key(now_utc: time.struct_time) -> str:
    return time.strftime("%Y-%m-%d", now_utc)


def _relative_log_path(logs_root: Path, log_path: Path) -> Path:
    return log_path.relative_to(logs_root)


def _safe_relative_log_path(logs_root: Path, log_path: Path) -> str:
    try:
        return str(_relative_log_path(logs_root.resolve(), log_path.resolve()))
    except ValueError:
        return str(log_path)


def _normalize_logger_name(name: str) -> str:
    candidate = name.strip() if name else ""
    if not candidate:
        return _APP_LOGGER_NAME

    normalized_candidate = candidate.lstrip(".")
    if not normalized_candidate:
        return _APP_LOGGER_NAME
    if normalized_candidate == _APP_LOGGER_NAME:
        return normalized_candidate
    if normalized_candidate.startswith(f"{_APP_LOGGER_NAME}."):
        return normalized_candidate
    return f"{_APP_LOGGER_NAME}.{normalized_candidate}"
