import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID

from app.models.analytics import ComparisonStats
from app.models.feedback import FeedbackRequest
from app.models.onboarding import OnboardingRequest, CEFRLevel
from app.models.user_requests import DeleteUserRequest, UserDataRequest

from .config import SETTINGS


def _get_connection() -> sqlite3.Connection:
    """Helper to get a configured database connection."""

    conn = sqlite3.connect(SETTINGS.database)
    conn.executescript("""
        PRAGMA foreign_keys = ON;
    """)
    return conn


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    """Yield a database connection that is always closed.

    Commits on success and rolls back on failure.
    """

    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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

    with database() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(schema_sql)

    return True


def _window_filter_sql(days: int | None) -> tuple[str, tuple[str, ...]]:
    """Build SQL filter and params for optional rolling window by assessment timestamp."""

    if not days:
        return "", ()

    return " AND a.created_at >= datetime('now', ?)", (f"-{days} days",)


def get_cohort_stats(guid: UUID, days: int | None = None) -> ComparisonStats | None:
    """Get cohort statistics and user percentile rank for a given CEFR level cohort.

    Returns:
        ComparisonStats: If the cohort meets the minimum size requirement, otherwise None.
    """

    window_sql, window_params = _window_filter_sql(days)

    query = f"""
        WITH target_user AS (
            SELECT guid, cefr_level
            FROM users
            WHERE guid = ?
            LIMIT 1
        ),
        cohort_user_averages AS (
            SELECT a.guid, AVG(a.proficiency) AS avg_score
            FROM assessments a
            JOIN users u ON u.guid = a.guid
            JOIN target_user tu ON 1 = 1
            WHERE u.cefr_level = tu.cefr_level
              AND a.proficiency IS NOT NULL
              {window_sql}
            GROUP BY a.guid
        ),
        target AS (
            SELECT
                tu.guid,
                tu.cefr_level,
                cua.avg_score AS target_avg
            FROM target_user tu
            LEFT JOIN cohort_user_averages cua ON cua.guid = tu.guid
        ),
        summary AS (
            SELECT
                COUNT(*) AS cohort_size
            FROM cohort_user_averages
        ),
        rank_calc AS (
            SELECT
                CASE
                    WHEN t.target_avg IS NULL THEN NULL
                    ELSE SUM(
                        CASE
                            WHEN cua.avg_score > t.target_avg
                              OR (cua.avg_score = t.target_avg AND cua.guid <= t.guid)
                            THEN 1
                            ELSE 0
                        END
                    )
                END AS row_id
            FROM target t
            LEFT JOIN cohort_user_averages cua ON 1 = 1
        )
        SELECT
            s.cohort_size,
            rc.row_id,
            tu.cefr_level
        FROM summary s
        LEFT JOIN rank_calc rc ON 1 = 1
        LEFT JOIN target_user tu ON 1 = 1;
    """

    params = (str(guid), *window_params)

    with database() as db:
        row = db.execute(query, params).fetchone()

    cohort_size = int(row[0] or 0) if row else 0
    if cohort_size < SETTINGS.min_cohort_size:
        return None

    user_row_id = int(row[1]) if row and row[1] is not None else None
    cefr_level = CEFRLevel(row[2]) if row and row[2] is not None else None

    if user_row_id is None or cefr_level is None:
        return None

    percentile = (cohort_size - user_row_id + 1) / cohort_size

    return ComparisonStats(
        cefr_level=cefr_level,
        cohort_size=cohort_size,
        percentile=round(percentile, 2),
        rank=user_row_id,
    )


def create_user(data: OnboardingRequest) -> None:
    """Inserts a new user record into the database based on the onboarding data."""

    # Format consent_timestamp as ISO 8601 string for storage
    consent_timestamp = data.consent_timestamp.isoformat()

    with database() as db:
        db.execute("""
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


def create_user_request(data: UserDataRequest) -> None:
    """Creates a new user request in the database.

    Args:
        data: UserDataRequest containing the user's GUID and request type
    """

    with database() as db:
        db.execute("""
            INSERT INTO user_requests (guid, type)
            VALUES (?, ?)
        """, (str(data.guid), data.type))


def delete_user_data(data: DeleteUserRequest) -> None:
    """Deletes all data associated with the given GUID.

    This is used to fulfill user data deletion requests.

    Args:
        guid: User's GUID
    """

    with database() as db:
        db.execute("DELETE FROM users WHERE guid = ?", (str(data.guid),))


def create_assessment_feedback(data: FeedbackRequest) -> None:
    """Inserts a new assessment-related feedback record into the database.

    Args:
        data: FeedbackRequest containing feedback details
    """

    query = """
        INSERT INTO feedback_assessment (
            guid,
            assessment_id,
            type,
            reaction_value,
            comment
        ) VALUES (?, ?, ?, ?, ?)
        """

    params = (
        str(data.guid),
        data.assessment_id,
        data.feedback_classification,
        data.reaction_value,
        data.comment
    )

    with database() as db:
        db.execute(query, params)


def create_experience_feedback(data: FeedbackRequest) -> None:
    """Inserts a new experience-related feedback record into the database.

    Args:
        data: FeedbackRequest containing feedback details
    """

    query = """
        INSERT INTO feedback_experience (
            guid,
            type,
            reaction_value,
            comment
        ) VALUES (?, ?, ?, ?)
        """

    params = (
        str(data.guid),
        data.feedback_classification,
        data.reaction_value,
        data.comment
    )

    with database() as db:
        db.execute(query, params)


def get_user(guid: UUID) -> bool:
    """Check whether a user row exists for a GUID.

    Args:
        guid: The user's GUID.

    Returns:
        True if a users row exists, otherwise False.
    """

    with database() as db:
        row = db.execute(
            "SELECT 1 FROM users WHERE guid = ? LIMIT 1", (str(guid),)
        ).fetchone()
    return row is not None


def get_user_consent(guid: UUID) -> bool:
    """Check whether a user has an accepted consent record.

    Args:
        guid: The user's GUID.

    Returns:
        True if a users row exists with consent_accepted=1, otherwise False.
    """

    with database() as db:
        row = db.execute(
            "SELECT 1 FROM users WHERE guid = ? AND consent_accepted = 1 LIMIT 1", (str(
                guid),)
        ).fetchone()
    return row is not None
