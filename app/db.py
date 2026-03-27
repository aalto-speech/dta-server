import json
import sqlite3
from pathlib import Path
from uuid import UUID

from app.models.feedback import FeedbackRequest
from app.models.onboarding import OnboardingRequest
from app.models.user_requests import DeleteUserRequest, UserDataRequest
from app.models.analytics import ComparisonStats, CohortType

from .config import SETTINGS


def _score_expression() -> str:
    """Return SQL expression for per-attempt score across all ASA dimensions."""

    return (
        "(a.accuracy + a.fluency + a.proficiency + a.pronunciation + a.range_score) / 5.0"
    )


def _window_filter_sql(days: int | None) -> tuple[str, tuple[str, ...]]:
    """Build SQL filter and params for optional rolling window by assessment timestamp."""

    if days is None:
        return "", ()

    return " AND a.created_at >= datetime('now', ?)", (f"-{days} days",)


def get_user_self_assessment_level(guid: UUID) -> str | None:
    """Get a user's self-assessed Finnish level used as default cohort selector."""

    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(
            "SELECT finnish_self_assessment FROM users WHERE guid = ? LIMIT 1",
            (str(guid),),
        ).fetchone()

    if row is None:
        return None

    return str(row[0])


def get_user_average_score(guid: UUID, days: int | None = None) -> float | None:
    """Compute a user's average performance score over all qualifying attempts."""

    score_expr = _score_expression()
    window_sql, window_params = _window_filter_sql(days)

    query = f"""
        WITH user_attempts AS (
            SELECT {score_expr} AS attempt_score
            FROM assessments a
            WHERE a.guid = ?
              AND a.accuracy IS NOT NULL
              AND a.fluency IS NOT NULL
              AND a.proficiency IS NOT NULL
              AND a.pronunciation IS NOT NULL
              AND a.range_score IS NOT NULL
              {window_sql}
        )
        SELECT ROUND(AVG(attempt_score), 4) AS user_average
        FROM user_attempts
    """

    params: tuple[str, ...] = (str(guid), *window_params)

    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(query, params).fetchone()

    if row is None or row[0] is None:
        return None

    return float(row[0])


def _get_distribution_summary(
    cohort_label: str,
    days: int | None = None,
) -> dict[str, int]:
    """Build coarse score buckets for cohort charting without exposing peer-level rows.

    Current Implementation (Phase 3+):
    - Returns fixed buckets: 0-1, 1-2, 2-3, 3-4, 4-5 (user average scores).
    - No privacy filtering: all buckets included regardless of count.

    Future Enhancement (Phase 5+):
    - Add config setting: MIN_DISTRIBUTION_BUCKET_SIZE (e.g., 5).
    - If any bucket count < MIN_DISTRIBUTION_BUCKET_SIZE, suppress distribution entirely.
    - Prevents re-identification risk when cohorts are near minimum threshold.
    - Update endpoint response to suppress distributionSummary when privacy rules triggered.

    To implement:
    1. Add MIN_DISTRIBUTION_BUCKET_SIZE to config.py (default: 5).
    2. Pass config value as parameter to this function.
    3. Check if min(bucket_counts) < threshold; return None instead of dict.
    4. Caller (get_comparison_stats_by_self_assessment) sets distribution_summary=None when filtered.
    """

    window_sql, window_params = _window_filter_sql(days)
    score_expr = _score_expression()

    query = f"""
        WITH cohort_attempts AS (
            SELECT a.guid, {score_expr} AS attempt_score
            FROM assessments a
            JOIN users u ON u.guid = a.guid
            WHERE u.finnish_self_assessment = ?
              AND a.accuracy IS NOT NULL
              AND a.fluency IS NOT NULL
              AND a.proficiency IS NOT NULL
              AND a.pronunciation IS NOT NULL
              AND a.range_score IS NOT NULL
              {window_sql}
        ),
        cohort_user_averages AS (
            SELECT guid, AVG(attempt_score) AS avg_score
            FROM cohort_attempts
            GROUP BY guid
        )
        SELECT
            SUM(CASE WHEN avg_score < 1 THEN 1 ELSE 0 END) AS bucket_0_1,
            SUM(CASE WHEN avg_score >= 1 AND avg_score < 2 THEN 1 ELSE 0 END) AS bucket_1_2,
            SUM(CASE WHEN avg_score >= 2 AND avg_score < 3 THEN 1 ELSE 0 END) AS bucket_2_3,
            SUM(CASE WHEN avg_score >= 3 AND avg_score < 4 THEN 1 ELSE 0 END) AS bucket_3_4,
            SUM(CASE WHEN avg_score >= 4 THEN 1 ELSE 0 END) AS bucket_4_5
        FROM cohort_user_averages
    """

    params: tuple[str, ...] = (cohort_label, *window_params)

    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(query, params).fetchone()

    if row is None:
        return {"0-1": 0, "1-2": 0, "2-3": 0, "3-4": 0, "4-5": 0}

    return {
        "0-1": int(row[0] or 0),
        "1-2": int(row[1] or 0),
        "2-3": int(row[2] or 0),
        "3-4": int(row[3] or 0),
        "4-5": int(row[4] or 0),
    }


def get_comparison_stats_by_self_assessment(
    guid: UUID,
    days: int | None = None,
) -> ComparisonStats:
    """Compute user-vs-cohort analytics with privacy-safe minimum-cohort gating."""

    cohort_label = get_user_self_assessment_level(guid)
    if cohort_label is None:
        return ComparisonStats(
            comparison_available=False,
            cohort_type=CohortType.SELF_ASSESSMENT,
            cohort_label="",
            cohort_size=0,
            user_average_score=None,
            cohort_average=None,
            percentile=None,
            distribution_summary=None,
        )

    window_sql, window_params = _window_filter_sql(days)
    score_expr = _score_expression()

    query = f"""
        WITH cohort_attempts AS (
            SELECT a.guid, {score_expr} AS attempt_score
            FROM assessments a
            JOIN users u ON u.guid = a.guid
            WHERE u.finnish_self_assessment = ?
              AND a.accuracy IS NOT NULL
              AND a.fluency IS NOT NULL
              AND a.proficiency IS NOT NULL
              AND a.pronunciation IS NOT NULL
              AND a.range_score IS NOT NULL
              {window_sql}
        ),
        cohort_user_averages AS (
            SELECT guid, AVG(attempt_score) AS avg_score
            FROM cohort_attempts
            GROUP BY guid
        ),
        target_user AS (
            SELECT avg_score AS target_score
            FROM cohort_user_averages
            WHERE guid = ?
            LIMIT 1
        )
        SELECT
            COUNT(*) AS cohort_size,
            ROUND(AVG(avg_score), 4) AS cohort_average,
            (SELECT ROUND(target_score, 4) FROM target_user) AS user_average,
            (
                SELECT ROUND((100.0 * SUM(CASE WHEN cua.avg_score <= tu.target_score THEN 1 ELSE 0 END)) / COUNT(*), 2)
                FROM cohort_user_averages cua
                CROSS JOIN target_user tu
            ) AS percentile
        FROM cohort_user_averages
    """

    params: tuple[str, ...] = (cohort_label, *window_params, str(guid))

    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(query, params).fetchone()

    cohort_size = int(row[0] or 0) if row else 0
    cohort_average = float(row[1]) if row and row[1] is not None else None
    user_average = float(row[2]) if row and row[2] is not None else None
    percentile = float(row[3]) if row and row[3] is not None else None

    comparison_available = (
        cohort_size >= SETTINGS.minimum_cohort_size
        and user_average is not None
    )

    if not comparison_available:
        return ComparisonStats(
            comparison_available=False,
            cohort_type=CohortType.SELF_ASSESSMENT,
            cohort_label=cohort_label,
            cohort_size=cohort_size,
            user_average_score=user_average,
            cohort_average=None,
            percentile=None,
            distribution_summary=None,
        )

    return ComparisonStats(
        comparison_available=True,
        cohort_type=CohortType.SELF_ASSESSMENT,
        cohort_label=cohort_label,
        cohort_size=cohort_size,
        user_average_score=user_average,
        cohort_average=cohort_average,
        percentile=percentile,
        distribution_summary=_get_distribution_summary(cohort_label, days),
    )


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


def create_user(data: OnboardingRequest) -> None:
    """Inserts a new user record into the database based on the onboarding data."""

    # Format consent_timestamp as ISO 8601 string for storage
    consent_timestamp = data.consent_timestamp.isoformat()

    conn = sqlite3.connect(SETTINGS.database)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (guid, consent_accepted, consent_timestamp, app_version, gender, age_group, native_languages, other_languages, moved_to_finland, finnish_learning_duration, finnish_self_assessment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
