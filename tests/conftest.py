from pathlib import Path

import pytest

from app.config import DATABASE


@pytest.fixture
def reset_database() -> None:
    """Reset DB file so tests start from a clean state."""

    db_path = Path(DATABASE)
    if db_path.exists():
        db_path.unlink()
