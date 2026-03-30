import sqlite3
from pathlib import Path
from uuid import UUID

from app.models.analytics import ComparisonRequest, ComparisonStats
from app.models.onboarding import CEFRLevel

from .config import SETTINGS


def database() -> sqlite3.Connection:
    """Helper to get a configured database connection."""

    conn = sqlite3.connect(database)
    conn.executescript("""
    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;
    """)
    return conn


def initialize_database() -> bool:
    """Initialize a database on app startup if it does not exist.

    Returns:
        bool: True if the database was created, False if it already existed.
    """

    db_path = Path(SETTINGS.database)
    if db_path.exists():
        return False

    schema_sql = Path(__file__).with_name(
        "schema.sql").read_text(encoding="utf-8")

    db = database()
    db.executescript(schema_sql)
    db.commit()
    db.close()

    return True


def _window_filter_sql(days: int | None) -> tuple[str, tuple[str, ...]]:
    """Build SQL filter and params for optional rolling window by assessment timestamp."""

    if days is None:
        return "", ()

    return " AND a.created_at >= datetime('now', ?)", (f"-{days} days",)

    return float(row[0])


def get_cohort_stats(guid: UUID, cefr_level: CEFRLevel) -> ComparisonStats | None:
    """Get cohort statistics and user percentile rank for a given CEFR level cohort."""

    window_sql, window_params = _window_filter_sql(days)

    query = f"""
        WITH cohort_user_averages AS (
            SELECT a.guid, AVG(a.proficiency) AS avg_score
            FROM assessments a
            JOIN users u ON u.guid = a.guid
            WHERE u.cefr_level = ?
              AND a.proficiency IS NOT NULL
              {window_sql}
            GROUP BY a.guid
        ),
        ranked AS (
            SELECT
                guid,
                ROW_NUMBER() OVER (ORDER BY avg_score DESC) AS row_id
            FROM cohort_user_averages
        ),
        summary AS (
            SELECT
                COUNT(*) AS cohort_size,
                ROUND(AVG(avg_score), 2) AS cohort_average
            FROM cohort_user_averages
        ),
        target AS (
            SELECT row_id
            FROM ranked
            WHERE guid = ?
            LIMIT 1
        )
        SELECT
            s.cohort_size,
            s.cohort_average,
            t.row_id
        FROM summary s
        LEFT JOIN target t ON 1 = 1;
    """

    params = (*window_params, cefr_level, str(guid))

    with database() as db:
        row = db.execute(query, params).fetchone()

    cohort_size = int(row[0] or 0) if row else 0
    if cohort_size < SETTINGS.min_cohort_size:
        return None

    cohort_average = float(row[1]) if row and row[1] is not None else None
    user_row_id = int(row[2]) if row and row[2] is not None else None

    if user_row_id is None:
        return None

    percentile = (cohort_size - user_row_id + 1) / cohort_size

    return ComparisonStats(
        cohort_label=cefr_level,
        cohort_size=cohort_size,
        cohort_average=cohort_average,
        percentile=round(percentile, 2),
    )


def create_user(data: OnboardingRequest) -> None:
    """Inserts a new user record into the database based on the onboarding data."""

    # Format consent_timestamp as ISO 8601 string for storage
    consent_timestamp = data.consent_timestamp.isoformat()

    db = database()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO users(guid, consent_accepted, consent_timestamp, app_version, gender, age_group, native_languages, other_languages, moved_to_finland, finnish_learning_duration, cefr_level)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(data.guid),
        int(data.consent_accepted),
        consent_timestamp,
        data.app_version,
        data.gender,
        data.age_group,
        json.dumps(data.native_languages),
        json.dumps(data.other_languages),
        data.moved_to_finland,
        data.finnish_learning_duration,
        data.finnish_self_assessment
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


def get_user(guid: UUID) -> bool:
    """Check whether a user row exists for a GUID.

    Args:
        guid: The user's GUID.

    Returns:
        True if a users row exists, otherwise False.
    """

    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE guid = ? LIMIT 1", (str(guid),)).fetchone()
    return row is not None


def get_user_consent(guid: UUID) -> bool:
    """Check whether a user has an accepted consent record.

    Args:
        guid: The user's GUID.

    Returns:
        True if a users row exists with consent_accepted=1, otherwise False.
    """

    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE guid = ? AND consent_accepted = 1 LIMIT 1", (str(guid),)).fetchone()
    return row is not None
