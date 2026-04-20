import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class AppEnv(StrEnum):
    """Supported application environments.

    Attributes:
        development: Local development environment with default settings.
        test: Isolated environment for running tests, using a separate database.
        staging: Pre-production environment for final testing, can mirror production settings.
        production: Live environment with strict requirements, such as a mandatory admin API key.
    """

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables.

    Attributes:
        env: Current application environment (e.g., development, production).
        database: Absolute path to the SQLite database file.
        audio_save_dir: Directory where uploaded audio files will be stored.
        logs_save_dir: Directory where log files will be stored.
        log_level: Logging level used by the application logger.
        admin_api_key: API key for admin operations, required in production.
        min_cohort_size: Minimum number of users required in a cohort for analytics to be returned.
        min_user_assessments: Minimum number of assessments a user must have for comparison analytics.
    """

    env: AppEnv
    database: str
    audio_save_dir: str
    logs_save_dir: str
    log_level: str
    admin_api_key: str
    min_cohort_size: int
    min_user_assessments: int


def _parse_app_env() -> AppEnv:
    raw_env = os.getenv("APP_ENV", AppEnv.DEVELOPMENT.value).strip().lower()
    try:
        return AppEnv(raw_env)
    except ValueError:
        return AppEnv.DEVELOPMENT


def _database_for_env(env: AppEnv) -> str:
    if env in {AppEnv.DEVELOPMENT, AppEnv.TEST}:
        return f"./{env}.db"

    return os.getenv("DATABASE", f"/data/{env}.db")


def _audio_save_dir_for_env(env: AppEnv) -> str:
    if env in {AppEnv.DEVELOPMENT, AppEnv.TEST}:
        return f"./audio/{env}"

    return os.getenv("AUDIO_SAVE_DIR", "/data/audio")


def _log_save_dir_for_env(env: AppEnv) -> str:
    if env in {AppEnv.DEVELOPMENT, AppEnv.TEST}:
        return f"./logs/{env}"

    return os.getenv("LOGS_SAVE_DIR", "/data/logs")


def _create_directory(path: str, mode: int = 0o700) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)


def _parse_int_env(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "Environment variable %s has invalid value %r (not an integer). Using default %d.",
            name, raw_value, default,
        )
        return default

    if value < minimum:
        logger.warning(
            "Value for %s (%d) is below minimum (%d). Using default %d.",
            name, value, minimum, default,
        )
        return default

    return value


def _build_settings() -> Settings:
    """Build settings once so the rest of the app can import stable values."""

    env = _parse_app_env()
    database = _database_for_env(env)
    if not database.startswith(":memory:"):
        _create_directory(os.path.dirname(database))

    audio_save_dir = _audio_save_dir_for_env(env)
    _create_directory(audio_save_dir)

    logs_save_dir = _log_save_dir_for_env(env)
    _create_directory(logs_save_dir)
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    admin_api_key = os.getenv("ADMIN_API_KEY", "")
    min_cohort_size = _parse_int_env(
        "MIN_COHORT_SIZE", default=100, minimum=2)
    min_user_assessments = _parse_int_env(
        "MIN_USER_ASSESSMENTS", default=3, minimum=1)

    if env == AppEnv.PRODUCTION and not admin_api_key:
        raise RuntimeError("ADMIN_API_KEY must be set in production")

    return Settings(
        env=env,
        database=database,
        audio_save_dir=audio_save_dir,
        logs_save_dir=logs_save_dir,
        log_level=log_level,
        admin_api_key=admin_api_key,
        min_cohort_size=min_cohort_size,
        min_user_assessments=min_user_assessments,
    )


SETTINGS = _build_settings()
