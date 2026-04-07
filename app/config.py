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
        admin_api_key: API key for admin operations, required in production.
        min_cohort_size: Minimum number of users required in a cohort for analytics to be returned.
        analytics_min_window_days: Minimum number of days for the rolling window filter in analytics.
        analytics_max_window_days: Maximum number of days for the rolling window filter in analytics.
    """

    env: AppEnv
    database: str
    admin_api_key: str
    min_cohort_size: int
    analytics_min_window_days: int
    analytics_max_window_days: int


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


def _create_database_parents(path: str) -> None:
    if path.startswith(":memory:"):
        return

    Path(path).parent.mkdir(parents=True, exist_ok=True)


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
    _create_database_parents(database)

    admin_api_key = os.getenv("ADMIN_API_KEY", "")
    min_cohort_size = _parse_int_env(
        "MIN_COHORT_SIZE", default=100, minimum=2)
    analytics_min_window_days = _parse_int_env(
        "ANALYTICS_MIN_WINDOW_DAYS", default=1, minimum=1)
    analytics_max_window_days = _parse_int_env(
        "ANALYTICS_MAX_WINDOW_DAYS", default=3650, minimum=1)

    analytics_max_window_days = max(
        analytics_max_window_days, analytics_min_window_days)

    if env == AppEnv.PRODUCTION and not admin_api_key:
        raise RuntimeError("ADMIN_API_KEY must be set in production")

    return Settings(
        env=env,
        database=database,
        admin_api_key=admin_api_key,
        min_cohort_size=min_cohort_size,
        analytics_min_window_days=analytics_min_window_days,
        analytics_max_window_days=analytics_max_window_days,
    )


SETTINGS = _build_settings()
