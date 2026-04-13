import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import SETTINGS
from app.models.analytics import ComparisonStats, GetCohortStatsInput
from app.models.feedback import (
    CreateAssessmentFeedbackInput,
    CreateExperienceFeedbackInput,
)
from app.models.onboarding import CEFRLevel, CreateUserInput
from app.models.speech_assessment import AssessmentCreateInput
from app.models.user_requests import (
    CreateUserRequestInput,
    DeleteUserDataInput,
    GetUserConsentInput,
    GetUserInput,
)


def _get_connection() -> sqlite3.Connection:
    """Create a SQLite connection with foreign keys enabled."""

    conn = sqlite3.connect(SETTINGS.database)
    conn.executescript("""
        PRAGMA foreign_keys = ON;
    """)
    return conn


def _window_filter_sql(days: int | None) -> tuple[str, tuple[str, ...]]:
    """Build optional SQL and params for a rolling assessment window."""

    if not days:
        return "", ()

    return " AND a.created_at >= datetime('now', ?)", (f"-{days} days",)


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    """Yield a DB connection and manage commit or rollback automatically."""

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
    """Initialize the database from schema if the DB file is missing."""

    db_path = Path(SETTINGS.database)
    if db_path.exists():
        return False

    schema_sql = Path(__file__).with_name(
        "schema.sql").read_text(encoding="utf-8")

    with database() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(schema_sql)

    return True


def create_assessment(data: AssessmentCreateInput) -> int | None:
    """Insert an assessment row and return its ID."""

    query = """
        INSERT INTO assessments (
            guid,
            task_id,
            audio_id,
            audio_path,
            transcript,
            accuracy,
            fluency,
            proficiency,
            pronunciation,
            range_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    params = (
        str(data.guid),
        data.task_id,
        str(data.audio_id),
        str(data.audio_path),
        data.transcript,
        data.accuracy,
        data.fluency,
        data.proficiency,
        data.pronunciation,
        data.range_score
    )

    with database() as db:
        cur = db.execute(query, params)
        assessment_id = cur.lastrowid

    return assessment_id


def get_cohort_stats(data: GetCohortStatsInput) -> ComparisonStats | None:
    """Return cohort stats and rank for the user within their CEFR cohort."""

    window_sql, window_params = _window_filter_sql(data.days)

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

    params = (str(data.guid), *window_params)

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


def create_user(data: CreateUserInput) -> None:
    """Insert a user row from onboarding data."""

    # Format consent_timestamp as ISO 8601 string for storage
    consent_timestamp = data.consent_timestamp.isoformat()

    query = """
        INSERT INTO users(
            guid,
            consent_accepted,
            consent_timestamp,
            app_version,
            gender,
            age_group,
            native_languages,
            other_languages,
            moved_to_finland,
            finnish_learning_duration,
            cefr_level
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    params = (
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
    )

    with database() as db:
        db.execute(query, params)


def create_user_request(data: CreateUserRequestInput) -> None:
    """Insert a user data request row."""

    query = """
        INSERT INTO user_requests (guid, type)
        VALUES (?, ?)
    """

    params = (
        str(data.guid),
        data.type
    )

    with database() as db:
        db.execute(query, params)


def delete_user_data(data: DeleteUserDataInput) -> None:
    """Delete a user row and rely on FK cascades for related data."""

    query = """
        DELETE FROM users WHERE guid = ?
    """

    params = (str(data.guid),)

    with database() as db:
        db.execute(query, params)


def create_assessment_feedback(data: CreateAssessmentFeedbackInput) -> None:
    """Insert assessment-related feedback."""

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


def create_experience_feedback(data: CreateExperienceFeedbackInput) -> None:
    """Insert experience-related feedback."""

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


def get_user(data: GetUserInput) -> bool:
    """Check whether a user exists."""

    query = """
        SELECT 1 FROM users WHERE guid = ? LIMIT 1
    """

    params = (str(data.guid),)

    with database() as db:
        row = db.execute(query, params).fetchone()

    return row is not None


def get_user_consent(data: GetUserConsentInput) -> bool:
    """Check whether a user has an accepted consent record."""

    query = """
        SELECT 1 FROM users WHERE guid = ? AND consent_accepted = 1 LIMIT 1
    """

    params = (str(data.guid),)

    with database() as db:
        row = db.execute(query, params).fetchone()

    return row is not None
