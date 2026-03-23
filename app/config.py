import os
from dataclasses import dataclass
from enum import StrEnum

from dotenv import load_dotenv

load_dotenv()


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
    """Centralized settings loaded from environment variables."""

    env: AppEnv
    database: str
    admin_api_key: str


def _parse_app_env() -> AppEnv:
    raw_env = os.getenv("APP_ENV", AppEnv.DEVELOPMENT.value).strip().lower()
    try:
        return AppEnv(raw_env)
    except ValueError:
        return AppEnv.DEVELOPMENT


def _database_for_env(env: AppEnv) -> str:
    if env == AppEnv.PRODUCTION:
        return os.getenv("DATABASE", "dta.db")
    return f"{env}.db"


def get_settings() -> Settings:
    """Build settings once so the rest of the app can import stable values."""

    env = _parse_app_env()
    admin_api_key = os.getenv("ADMIN_API_KEY", "")

    if env == AppEnv.PRODUCTION and not admin_api_key:
        raise RuntimeError("ADMIN_API_KEY must be set in production")

    return Settings(
        env=env,
        database=_database_for_env(env),
        admin_api_key=admin_api_key,
    )


SETTINGS = get_settings()
