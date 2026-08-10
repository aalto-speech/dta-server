import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import SETTINGS
from app.models.analytics import (
    AssessmentUnavailable,
    CohortSizeTooLow,
    ComparisonStats,
    ComparisonUnavailable,
    DayWindow,
    GetCohortStatsInput,
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
from app.models.users import SetUserCEFRLevelInput


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
    """Look up the level the user is CURRENTLY working at, or raise when they are missing.

    COALESCE, not a plain read: `current_cefr_level` is NULL until the learner moves
    themselves with PATCH /users/level, and NULL means "never moved", so it falls back to
    the onboarding self-assessment. Every read of "what level is this user" goes through
    here so the two columns can never drift apart in one caller and not another.
    """

    row = db.execute(
        "SELECT COALESCE(current_cefr_level, cefr_level) FROM users WHERE guid = ? LIMIT 1",
        (guid,),
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
            range_score,
            content_relevance,
            content_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        data.range_score,
        data.content_relevance,
        data.content_confidence
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
                cefr_level=CEFRLevel(cefr_level),
                required_assessments=SETTINGS.min_user_assessments,
                current_assessments=assessment_count,
            )

        # Get all users in the same CEFR cohort with their average proficiency scores
        # Order by average score (descending) and guid (ascending) for tie-breaking
        cohort_query = """
            SELECT a.guid, AVG(a.proficiency) AS avg_score
            FROM assessments a
            WHERE guid IN (
                -- The cohort follows the level the learner is working at, not the one they
                -- guessed at sign-up, so Advance/Revert actually moves who they are ranked
                -- against. Same COALESCE as _get_user_cefr_level, for the same reason.
                SELECT guid FROM users
                WHERE COALESCE(current_cefr_level, cefr_level) = ?
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
            cefr_level=CEFRLevel(cefr_level)
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
        # NULL means "not collected"; json.dumps(None) would store the string 'null',
        # which is valid JSON but not an array, so it would trip the CHECK.
        json.dumps(data.native_languages) if data.native_languages is not None else None,
        json.dumps(data.other_languages) if data.other_languages is not None else None,
        data.moved_to_finland,
        data.finnish_learning_duration,
        data.finnish_self_assessment
    )

    with database() as db:
        db.execute(query, params)
        # Seed the trail with where the learner started, in the same transaction as the
        # user row. Without this the history begins at the first Advance/Revert and there
        # is nothing to say what it moved away from.
        db.execute(
            "INSERT INTO user_cefr_history (guid, cefr_level, source) VALUES (?, ?, ?)",
            (str(data.guid), data.finnish_self_assessment, "self_report"),
        )


def set_user_cefr_level(data: SetUserCEFRLevelInput) -> bool:
    """Move the level a user is working at. Returns False when the user does not exist.

    Writes `users.current_cefr_level` and appends to `user_cefr_history` in one
    transaction. `users.cefr_level` -- the onboarding self-assessment -- is never touched:
    see the column comment in schema.sql for why that matters.

    Idempotent by construction, because the client sends a target level rather than a
    direction: replaying the same request lands the user in the same place instead of
    moving them twice. A repeat still appends a history row, which is the honest record --
    the learner did press the button again.
    """

    with database() as db:
        updated = db.execute(
            "UPDATE users SET current_cefr_level = ? WHERE guid = ?",
            (data.cefr_level, str(data.guid)),
        ).rowcount

        if not updated:
            return False

        db.execute(
            "INSERT INTO user_cefr_history (guid, cefr_level, source) VALUES (?, ?, ?)",
            (str(data.guid), data.cefr_level, "self_report"),
        )

    return True


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
    """Insert feedback, or update the learner's existing answer to the same question.

    One learner answering one question about one recording is one row. The 2.0.0 client
    sends each answer the moment it is given rather than on a submit button, so a learner
    who taps an emoji, types a comment and then changes the emoji posts three times -- and
    all three are the same answer at different moments, not three opinions.

    The conflict target is the unique index in schema.sql. It covers only rows with an
    assessment_id, because SQLite treats NULLs as distinct: `comparison_ui` and
    `overall_experience` fall through to a plain insert, which is correct -- they are sent
    once, on a button, and carry no assessment to be scoped to.

    `comment` is overwritten unconditionally, including back to NULL. The client sends the
    whole answer every time, so an absent comment means the learner cleared it, not that
    they left it alone.
    """

    query = """
        INSERT INTO feedback (
            guid,
            assessment_id,
            type,
            reaction_value,
            comment
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (guid, assessment_id, type) DO UPDATE SET
            reaction_value = excluded.reaction_value,
            comment = excluded.comment,
            updated_at = CURRENT_TIMESTAMP
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
