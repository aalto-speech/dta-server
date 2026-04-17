import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import SETTINGS
from app.models.analytics import (
    ComparisonStats,
    ComparisonUnavailable,
    DayWindow,
    GetCohortStatsInput,
    AssessmentUnavailable,
    CohortSizeTooLow,
    NoRankAvailable,
)
from app.models.feedback import CreateFeedbackInput
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


def _window_filter_sql(days: DayWindow | None) -> tuple[str, tuple[str, ...]]:
    """Build optional SQL and params for a rolling assessment window."""

    if not days:
        return "", ()

    return " AND a.created_at >= datetime('now', ?)", (f"-{days.value} days",)


def _get_user_cefr_level(db: sqlite3.Connection, guid: str) -> str:
    """Look up the user's CEFR level or raise when the user is missing."""

    row = db.execute(
        "SELECT cefr_level FROM users WHERE guid = ? LIMIT 1", (guid,)
    ).fetchone()

    if not row:
        raise ValueError("User not found")

    return row[0]


def _count_scored_assessments(
    db: sqlite3.Connection,
    guid: str,
) -> int:
    """Count scored assessments for a user within the requested window."""

    query = """
        SELECT COUNT(*)
        FROM assessments a
        WHERE a.guid = ?
        AND a.proficiency IS NOT NULL
    """

    return db.execute(query, (guid,)).fetchone()[0]


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


def get_cohort_stats(
    data: GetCohortStatsInput,
) -> ComparisonStats | ComparisonUnavailable:
    """Return cohort stats and rank for the user within their CEFR cohort."""

    target_guid = str(data.guid)

    with database() as db:
        cefr_level = _get_user_cefr_level(db, target_guid)

        # Require enough scored assessments for the requesting user.
        assessment_count = _count_scored_assessments(
            db, target_guid
        )

        if assessment_count < SETTINGS.min_user_assessments:
            return AssessmentUnavailable(
                status="USER_ASSESSMENT_DATA_INSUFFICIENT",
                message=(
                    "User does not have enough scored assessments "
                    "for comparison statistics"
                ),
                required_assessments=SETTINGS.min_user_assessments,
                current_assessments=assessment_count,
            )

        # Get all users in the same CEFR cohort with their average proficiency scores
        # Order by average score (descending) and guid (ascending) for tie-breaking
        cohort_query = """
            SELECT a.guid, AVG(a.proficiency) AS avg_score
            FROM assessments a
            WHERE guid IN (
                SELECT guid FROM users WHERE cefr_level = ?
            )
            AND proficiency IS NOT NULL
            GROUP BY a.guid
            ORDER BY avg_score DESC, guid ASC
        """

        cohort_rows = db.execute(
            cohort_query, (cefr_level,)).fetchall()

    cohort_size = len(cohort_rows)

    if cohort_size < SETTINGS.min_cohort_size:
        return CohortSizeTooLow(
            status="COHORT_SIZE_TOO_SMALL",
            message=(
                "Comparison statistics are not available for your cohorts size at this time."
            ),
            cohort_size=cohort_size,
        )

    # Find the rank of the target user (1-indexed position in sorted list)
    rank = None
    for i, (guid, _) in enumerate(cohort_rows, 1):
        if guid == target_guid:
            rank = i
            break

    if not rank:
        return NoRankAvailable(
            status="RANK_UNAVAILABLE",
            message=(
                "Unable to determine rank for the user within the cohort at this time."
            ),
        )

    # Calculate percentile
    percentile = (cohort_size - rank) / cohort_size

    return ComparisonStats(
        cefr_level=CEFRLevel(cefr_level),
        cohort_size=cohort_size,
        percentile=round(percentile, 2),
        rank=rank,
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


def create_feedback(data: CreateFeedbackInput) -> None:
    """Insert feedback."""

    query = """
        INSERT INTO feedback (
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
