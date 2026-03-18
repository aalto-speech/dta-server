import json
import sqlite3


def create_user(payload):
    """Inserts a new user record into the database based on the onboarding payload."""

    conn = sqlite3.connect('speech_assessments.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (guid, consent_accepted, consent_timestamp, app_version, gender, age_group, mother_tongues, other_languages, moved_to_finland, finnish_learning_duration, finnish_self_assessment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        str(payload.guid),
        int(payload.consent_accepted),
        payload.consent_timestamp,
        payload.app_version,
        payload.gender,
        payload.age_group,
        json.dumps(payload.mother_tongues, separators=(",", ":")),
        json.dumps(payload.other_languages, separators=(",", ":")),
        payload.moved_to_finland,
        payload.finnish_learning_duration,
        payload.finnish_self_assessment
    ))
    conn.commit()
    conn.close()
