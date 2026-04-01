import os
from pathlib import Path
from typing import Iterator

import pytest

# Set test environment before any app config is imported in test collection.
os.environ["APP_ENV"] = "test"

from app.config import SETTINGS  # noqa pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def isolate_test_database() -> Iterator[None]:
    """Ensure every test gets a clean SQLite database state.

    Also remove SQLite sidecar files created when WAL mode is enabled.
    """
    db_path = Path(SETTINGS.database)
    db_path.unlink(missing_ok=True)

    # Run test body.
    yield

    db_path = Path(SETTINGS.database)
    db_path.unlink(missing_ok=True)
    Path(f"{db_path}-wal").unlink(missing_ok=True)
    Path(f"{db_path}-shm").unlink(missing_ok=True)
