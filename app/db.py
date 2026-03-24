import json
import sqlite3
from pathlib import Path
from uuid import UUID

from app.models.feedback import FeedbackRequest
from app.models.onboarding import OnboardingRequest
from app.models.user_requests import DeleteUserRequest, UserDataRequest

from .config import SETTINGS


def initialize_database() -> bool:
    """Initialize a database on app startup if it does not exist.

    Returns:
        bool: True if the database was created, False if it already existed.
    """

    db_path = Path(SETTINGS.database)
    if db_path.exists():
        return False

    conn = sqlite3.connect(SETTINGS.database)
    conn.execute("PRAGMA journal_mode=WAL;")
    schema_sql = Path(__file__).with_name(
        "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()

    return True


def create_user(payload: OnboardingRequest) -> None:
    """Inserts a new user record into the database based on the onboarding payload."""

    conn = sqlite3.connect(SETTINGS.database)
    cursor = conn.cursor()
    consent_timestamp = payload.consent_timestamp.isoformat()
    cursor.execute("""
        INSERT INTO users (guid, consent_accepted, consent_timestamp, app_version, gender, age_group, mother_tongues, other_languages, moved_to_finland, finnish_learning_duration, finnish_self_assessment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(payload.guid),
        int(payload.consent_accepted),
        consent_timestamp,
        payload.app_version,
        payload.background_fields.gender,
        payload.background_fields.age_group,
        json.dumps(payload.background_fields.native_languages,
                   separators=(",", ":")),
        json.dumps(payload.background_fields.other_languages,
                   separators=(",", ":")),
        payload.background_fields.moved_to_finland,
        payload.background_fields.finnish_learning_duration,
        payload.background_fields.finnish_self_assessment
    ))
    conn.commit()
    conn.close()


def create_user_request(data: UserDataRequest) -> None:
    """Creates a new user request in the database.

    Args:
        data: UserDataRequest containing the user's GUID and request type
    """

    conn = sqlite3.connect(SETTINGS.database)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_requests (guid, type)
        VALUES (?, ?)
    """, (str(data.guid), data.type))
    conn.commit()
    conn.close()


def delete_user_data(data: DeleteUserRequest) -> None:
    """Deletes all data associated with the given GUID.

    This is used to fulfill user data deletion requests.

    Args:
        guid: User's GUID
    """

    conn = sqlite3.connect(SETTINGS.database)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE guid = ?", (str(data.guid),))
    conn.commit()
    conn.close()


def create_feedback(data: FeedbackRequest) -> None:
    """Inserts a new feedback record into the database.

    Args:
        data: FeedbackRequest containing feedback details
    """

    conn = sqlite3.connect(SETTINGS.database)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO feedback (
        guid,
        assessment_id,
        type,
        reaction_value,
        comment
    ) VALUES (?, ?, ?, ?, ?)
    """, (
        str(data.guid),
        data.assessment_id,
        data.type,
        data.reaction_value,
        data.comment
    ))

    conn.commit()
    conn.close()


def get_user_guid(guid: UUID) -> bool:
    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE guid = ? LIMIT 1", (str(guid),)).fetchone()
    return row is not None


def get_user_consent(guid: UUID) -> bool:
    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(
            "SELECT consent_accepted FROM users WHERE guid = ? LIMIT 1", (str(guid),)).fetchone()
    return True if row == "1" else False
