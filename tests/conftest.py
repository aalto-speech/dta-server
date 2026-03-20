from pathlib import Path

import pytest

from app.config import SETTINGS


@pytest.fixture
def reset_database() -> None:
    """Reset DB file so tests start from a clean state."""

    db_path = Path(SETTINGS.database)
    db_path.unlink(missing_ok=True)
