import json
import os
import sqlite3
from uuid import UUID

from dotenv import load_dotenv
load_dotenv()

DATABASE = os.getenv("DATABASE", "dta.db")


def create_user(payload):
    """Inserts a new user record into the database based on the onboarding payload."""

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (guid, consent_accepted, consent_timestamp, app_version, gender, age_group, mother_tongues, other_languages, moved_to_finland, finnish_learning_duration, finnish_self_assessment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        str(payload.guid),
        int(payload.consent_accepted),
        payload.consent_timestamp,
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


def create_user_request(guid: UUID, request_type: str) -> None:
    """Creates a new user request in the database.

    Args:
        guid: User's GUID
        request_type: Type of request ('delete_data', 'data_export', etc.)
    """
    conn = sqlite3.connect('speech_assessments.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_requests (guid, type)
        VALUES (?, ?)
    ''', (str(guid), request_type))
    conn.commit()
    conn.close()
