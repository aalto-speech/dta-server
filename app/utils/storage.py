"""Filesystem layout for user-owned data.

Recordings are the only user data the app keeps outside SQLite, one directory per
GUID under `AUDIO_SAVE_DIR`. Both the write path (speech assessment) and the erase
path (user deletion) resolve locations through here, so the layout is defined once.
"""
import shutil
from pathlib import Path
from uuid import UUID

from app.config import SETTINGS
from app.utils.logger import get_logger

logger = get_logger(__name__)


def user_audio_dir(guid: UUID) -> Path:
    """Return the directory holding one user's recordings (may not exist)."""

    return Path(SETTINGS.audio_save_dir) / str(guid)


def delete_user_audio(guid: UUID) -> int:
    """Delete every recording belonging to a user.

    Args:
        guid: The user's GUID.

    Returns:
        int: Number of files removed. Zero when the user never uploaded audio.

    Raises:
        ValueError: If the resolved path is not a direct child of the audio root.
        OSError: If the files exist but cannot be removed.
    """

    root = Path(SETTINGS.audio_save_dir).resolve()
    target = (root / str(guid)).resolve()

    # shutil.rmtree is unforgiving, so refuse anything that is not a direct child of
    # the audio root regardless of what the GUID string expanded to.
    if target == root or target.parent != root:
        raise ValueError(f"Refusing to delete audio outside {root}: {target}")

    if not target.is_dir():
        return 0

    removed = sum(1 for path in target.rglob("*") if path.is_file())
    shutil.rmtree(target)
    return removed
