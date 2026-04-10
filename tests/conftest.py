import os
import shutil
from pathlib import Path
from typing import Iterator

import pytest

# Set test environment before any app config is imported in test collection.
os.environ["APP_ENV"] = "test"

from app.config import SETTINGS  # noqa pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def isolate_test_database() -> Iterator[None]:
    """Ensure every test gets a clean SQLite database state."""

    db_path = Path(SETTINGS.database)
    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")
    audio_test_dir = Path(SETTINGS.audio_save_dir)

    def _cleanup_test_artifacts() -> None:
        db_path.unlink(missing_ok=True)
        wal_path.unlink(missing_ok=True)
        shm_path.unlink(missing_ok=True)
        shutil.rmtree(audio_test_dir, ignore_errors=True)

    _cleanup_test_artifacts()

    try:
        # Run test body.
        yield
    finally:
        _cleanup_test_artifacts()
