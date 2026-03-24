import sqlite3
from uuid import uuid4

from app.config import SETTINGS
from app.db import (
    get_comparison_stats_by_self_assessment,
    get_user_average_score,
    initialize_database,
)


def _insert_user(cursor: sqlite3.Cursor, guid: str, level: str = "B1") -> None:
    cursor.execute(
        """
        INSERT INTO users (
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
            finnish_self_assessment
        ) VALUES (?, 1, '2026-01-01T00:00:00', '1.0.0', 'woman', 'age_29_39',
                  '["Finnish"]', '[]', 'before_2015', 'months_6_9', ?)
        """,
        (guid, level),
    )


def _insert_assessment(
    cursor: sqlite3.Cursor,
    guid: str,
    score: float,
    created_at: str,
    suffix: str,
) -> None:
    cursor.execute(
        """
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
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guid,
            f"task-{suffix}",
            f"audio-{suffix}",
            f"/tmp/audio-{suffix}.wav",
            "sample",
            score,
            score,
            score,
            score,
            score,
            created_at,
        ),
    )


def test_get_user_average_score_computes_mean_across_attempts(reset_database):
    """User score uses average across multiple assessment attempts."""

    _ = reset_database
    initialize_database()
    guid = uuid4()

    with sqlite3.connect(SETTINGS.database) as conn:
        cursor = conn.cursor()
        _insert_user(cursor, str(guid))
        _insert_assessment(cursor, str(guid), 3.0, "2026-03-20 10:00:00", "u1")
        _insert_assessment(cursor, str(guid), 5.0, "2026-03-21 10:00:00", "u2")
        conn.commit()

    user_average = get_user_average_score(guid=guid)

    assert user_average == 4.0


def test_comparison_stats_hidden_when_cohort_is_below_privacy_threshold(reset_database):
    """Comparison remains unavailable when cohort size does not meet minimum threshold."""

    _ = reset_database
    initialize_database()

    with sqlite3.connect(SETTINGS.database) as conn:
        cursor = conn.cursor()
        for idx in range(1, SETTINGS.minimum_cohort_size):
            guid = uuid4()
            _insert_user(cursor, str(guid))
            _insert_assessment(
                cursor,
                str(guid),
                3.0,
                f"2026-03-{idx:02d} 10:00:00",
                f"small-{idx}",
            )
        conn.commit()

    target_guid = guid
    stats = get_comparison_stats_by_self_assessment(target_guid)

    assert stats.comparison_available is False
    assert stats.cohort_size == SETTINGS.minimum_cohort_size - 1
    assert stats.cohort_average is None
    assert stats.percentile is None
    assert stats.distribution_summary is None


def test_comparison_stats_returns_percentile_when_privacy_threshold_is_met(reset_database):
    """Comparison includes percentile and cohort average when cohort is large enough."""

    _ = reset_database
    initialize_database()
    target_guid = uuid4()

    with sqlite3.connect(SETTINGS.database) as conn:
        cursor = conn.cursor()

        _insert_user(cursor, str(target_guid))
        _insert_assessment(cursor, str(target_guid), 4.0,
                           "2026-03-10 10:00:00", "target")

        for idx in range(SETTINGS.minimum_cohort_size - 1):
            guid = uuid4()
            _insert_user(cursor, str(guid))
            _insert_assessment(
                cursor,
                str(guid),
                2.0,
                f"2026-03-{idx + 1:02d} 10:00:00",
                f"peer-{idx}",
            )

        conn.commit()

    stats = get_comparison_stats_by_self_assessment(target_guid)

    assert stats.comparison_available is True
    assert stats.cohort_size == SETTINGS.minimum_cohort_size
    assert stats.user_average_score == 4.0
    assert stats.cohort_average == 2.2
    assert stats.percentile == 100.0
    assert stats.distribution_summary is not None


def test_comparison_stats_respects_configurable_days_window(reset_database):
    """Rolling window excludes old assessments from user and cohort averages."""

    _ = reset_database
    initialize_database()
    target_guid = uuid4()

    with sqlite3.connect(SETTINGS.database) as conn:
        cursor = conn.cursor()

        _insert_user(cursor, str(target_guid))
        _insert_assessment(cursor, str(target_guid), 5.0,
                           "2099-01-01 10:00:00", "target-new")
        _insert_assessment(cursor, str(target_guid), 1.0,
                           "2000-01-01 10:00:00", "target-old")

        for idx in range(SETTINGS.minimum_cohort_size - 1):
            guid = uuid4()
            _insert_user(cursor, str(guid))
            _insert_assessment(
                cursor,
                str(guid),
                3.0,
                "2099-01-01 09:00:00",
                f"window-peer-{idx}",
            )
            _insert_assessment(
                cursor,
                str(guid),
                1.0,
                "2000-01-01 09:00:00",
                f"window-peer-old-{idx}",
            )

        conn.commit()

    all_time_stats = get_comparison_stats_by_self_assessment(target_guid)
    recent_stats = get_comparison_stats_by_self_assessment(
        target_guid, days=30)

    assert all_time_stats.user_average_score == 3.0
    assert recent_stats.user_average_score == 5.0
    assert all_time_stats.cohort_average == 2.1
    assert recent_stats.cohort_average == 3.2
