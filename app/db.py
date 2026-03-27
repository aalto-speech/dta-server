import sqlite3
from uuid import UUID

from app.models.analytics import ComparisonRequest, ComparisonStats

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
            "SELECT cefr_level FROM user_cefr_history WHERE guid = ? LIMIT 1 ORDER BY created_at DESC",
            (str(guid),),
        ).fetchone()

    if row is None:
        return None

    return str(row[0])


def get_user_average_score(data: ComparisonRequest) -> float | None:
    """Compute a user's average proficiency score over all qualifying attempts."""

    window_sql, window_params = _window_filter_sql(data.days)

    query = f"""
        SELECT AVG(a.proficiency)
        FROM assessments a
        WHERE a.guid = ?
            AND a.proficiency IS NOT NULL
            {window_sql}
    """

    params: tuple[str, ...] = (str(data.guid), *window_params)

    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(query, params).fetchone()

    if not row or not row[0]:
        return None

    return float(row[0])


def get_cohort_comparison_rank(guid: UUID) -> ComparisonStats | None:
    """Get comparison stats for a user against their cohort."""

    query = """
        SELECT ROUND(AVG(proficiency), 2) AS avg
        FROM assessments
        WHERE proficiency IS NOT NULL
        GROUP BY guid
        ORDER BY avg DESC;
    """

    with sqlite3.connect(SETTINGS.database) as conn:
        row = conn.execute(query).fetchall()


def get_comparison_stats_by_self_assessment(
    guid: UUID,
    days: int | None = None,
) -> ComparisonStats | None:
    """Compute user-vs-cohort analytics with privacy-safe minimum-cohort gating."""

    cohort_label = get_user_self_assessment_level(guid)
    if cohort_label is None:
        return ComparisonStats(
            cohort_label="",
            cohort_size=0,
            user_average_score=None,
            cohort_average=None,
            percentile=None,
        )

    window_sql, window_params = _window_filter_sql(days)

    # query = f"""
    #     WITH cohort_attempts AS(
    #         SELECT ROUND(AVG(proficiency), 2) AS avg
    #         FROM assessments
    #         WHERE proficiency IS NOT NULL
    #         {window_sql}
    #         GROUP BY guid
    #         ORDER BY avg DESC;
    #     ),
    #     cohort_user_averages AS(
    #         SELECT guid, AVG(attempt_score) AS avg_score
    #         FROM cohort_attempts
    #         GROUP BY guid
    #     ),
    #     target_user AS(
    #         SELECT avg_score AS target_score
    #         FROM cohort_user_averages
    #         WHERE guid=?
    #         LIMIT 1
    #     )
    #     SELECT
    #         COUNT(*) AS cohort_size,
    #         ROUND(AVG(avg_score), 4) AS cohort_average,
    #         (SELECT ROUND(target_score, 4) FROM target_user) AS user_average,
    #         (
    #             SELECT ROUND((100.0 * SUM(CASE WHEN cua.avg_score <= tu.target_score THEN 1 ELSE 0 END)) / COUNT(*), 2)
    #             FROM cohort_user_averages cua
    #             CROSS JOIN target_user tu
    #         ) AS percentile
    #     FROM cohort_user_averages
    # """

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

    return ComparisonStats(
        cohort_type=CohortType.SELF_ASSESSMENT,
        cohort_label=cohort_label,
        cohort_size=cohort_size,
        user_average_score=user_average,
        cohort_average=cohort_average,
        percentile=percentile,
        distribution_summary=_get_distribution_summary(
            cohort_label, days)
    ) if comparison_available else None


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
        INSERT INTO users(guid, consent_accepted, consent_timestamp, app_version, gender, age_group, native_languages, other_languages, moved_to_finland, finnish_learning_duration, finnish_self_assessment)
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
