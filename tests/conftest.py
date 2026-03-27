from pathlib import Path
from typing import Iterator

import pytest

from app.config import SETTINGS


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
