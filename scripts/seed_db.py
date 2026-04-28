#!/usr/bin/env python3
"""Seed the DigiTala SQLite database with large synthetic datasets.

Usage examples:
  python3 scripts/seed_db.py --truncate
  python3 scripts/seed_db.py --users 20000 --min-assessments 2 --max-assessments 10 --truncate
  python3 scripts/seed_db.py --db development.db --seed 1234
  python3 scripts/seed_db.py --users 0 --fixed-guid 00000000-0000-0000-0000-000000000001 --fixed-assessments 10
  python3 scripts/seed_db.py --db development.db --users 1000 --feedback-rate 0.2 --request-rate 0.05
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

GENDERS = ["woman", "man", "other", "prefer_not_to_answer"]
AGE_GROUPS = [
    "age_18_28",
    "age_29_39",
    "age_40_50",
    "age_51_61",
    "age_62_plus",
]
CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1_plus"]
NATIVE_LANGUAGE_POOL = [
    "Finnish",
    "English",
    "Swedish",
    "Arabic",
    "Russian",
    "Estonian",
    "Spanish",
    "Ukrainian",
    "Vietnamese",
    "Kurdish",
]
OTHER_LANGUAGE_POOL = [
    "English",
    "Swedish",
    "German",
    "French",
    "Spanish",
    "Russian",
    "Arabic",
    "Estonian",
]
FINNISH_LEARNING_DURATION = [
    "months_0_3",
    "months_3_6",
    "months_6_9",
    "months_9_12",
    "years_1_1.5",
    "years_1.5_2",
    "years_2_2.5",
    "years_2.5_3",
    "years_3_5",
    "years_5_7",
    "years_7_10",
    "years_10_plus",
]
FEEDBACK_ASSESSMENT_TYPES = [
    "self_assessment",
    "result_accuracy",
    "result_understanding",
]
FEEDBACK_EXPERIENCE_TYPES = ["comparison_ui", "overall_experience"]
REQUEST_TYPES = ["delete", "export"]
REQUEST_STATUS = ["pending", "approved", "denied", "completed"]
CEFR_HISTORY_SOURCE = ["self_report", "model"]


def _to_sql_timestamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _random_timestamp(rng: random.Random, now: datetime, days_back: int) -> datetime:
    window_seconds = max(days_back, 1) * 24 * 60 * 60
    return now - timedelta(seconds=rng.randint(0, window_seconds))


def _random_timestamp_current_year(rng: random.Random, now: datetime) -> datetime:
    start_of_year = datetime(now.year, 1, 1)
    window_seconds = int((now - start_of_year).total_seconds())
    return start_of_year + timedelta(seconds=rng.randint(0, max(window_seconds, 0)))


def _random_languages(rng: random.Random, pool: list[str], min_count: int, max_count: int) -> str:
    count = rng.randint(min_count, min(max_count, len(pool)))
    return json.dumps(rng.sample(pool, k=count))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed SQLite with large synthetic data.")
    parser.add_argument("--db", default="development.db",
                        help="SQLite file path (default from app settings)")
    parser.add_argument("--users", type=int, default=5000,
                        help="Number of users to insert")
    parser.add_argument("--fixed-guid", type=str,
                        default="00000000-0000-0000-0000-000000000001",
                        help="Optional specific GUID to insert as one additional user")
    parser.add_argument("--fixed-assessments", type=int, default=15,
                        help="Number of assessments to generate for --fixed-guid")
    parser.add_argument("--min-assessments", type=int,
                        default=3, help="Minimum assessments per user")
    parser.add_argument("--max-assessments", type=int,
                        default=12, help="Maximum assessments per user")
    parser.add_argument("--feedback-rate", type=float, default=0.30,
                        help="Chance of feedback per assessment (0-1)")
    parser.add_argument("--request-rate", type=float, default=0.04,
                        help="Chance of a user request per user (0-1)")
    parser.add_argument("--history-max", type=int, default=3,
                        help="Maximum CEFR history entries per user")
    parser.add_argument("--days-back", type=int, default=730,
                        help="How far back timestamps may go")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    parser.add_argument("--truncate", action="store_true",
                        help="Delete existing rows before seeding")
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if args.users < 0:
        raise ValueError("--users must be >= 0")
    if args.min_assessments < 1:
        raise ValueError("--min-assessments must be >= 1")
    if args.max_assessments < args.min_assessments:
        raise ValueError("--max-assessments must be >= --min-assessments")
    if not 0 <= args.feedback_rate <= 1:
        raise ValueError("--feedback-rate must be between 0 and 1")
    if not 0 <= args.request_rate <= 1:
        raise ValueError("--request-rate must be between 0 and 1")
    if args.history_max < 1:
        raise ValueError("--history-max must be >= 1")
    if args.days_back < 1:
        raise ValueError("--days-back must be >= 1")
    if args.fixed_assessments < 0:
        raise ValueError("--fixed-assessments must be >= 0")
    if args.fixed_assessments > 0 and not args.fixed_guid:
        raise ValueError(
            "--fixed-guid is required when --fixed-assessments > 0")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).resolve().parents[1] / "app" / "schema.sql"

    if not schema_path.is_file():
        schema_path = Path(__file__).resolve().parent / "schema.sql"

    if not schema_path.is_file():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    conn.executescript(schema_path.read_text(encoding="utf-8"))


def _truncate_tables(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM feedback_assessment")
    conn.execute("DELETE FROM feedback_experience")
    conn.execute("DELETE FROM user_requests")
    conn.execute("DELETE FROM user_cefr_history")
    conn.execute("DELETE FROM assessments")
    conn.execute("DELETE FROM users")


def main() -> None:
    """Main function to seed the database with synthetic data based on provided arguments."""

    args = _parse_args()
    _validate_args(args)
    rng = random.Random(args.seed)
    now = datetime.now()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    users_rows: list[tuple[str, int, str, str, str,
                           str, str, str, str, str, str, str]] = []
    assessments_rows: list[tuple[str, str, str, str,
                                 str, float, float, float, float, float, str]] = []
    history_rows: list[tuple[str, str, str, str]] = []
    request_rows: list[tuple[str, str, str, str, str | None, str | None]] = []
    user_ids: list[str] = []

    for _ in range(args.users):
        guid = str(uuid4())
        user_ids.append(guid)
        cefr_level = rng.choices(CEFR_LEVELS, weights=[
                                 18, 24, 30, 20, 8], k=1)[0]

        created_at = _random_timestamp(rng, now, args.days_back)
        consent_ts = created_at.isoformat(timespec="seconds")
        moved_to_finland = "before_2015" if rng.random(
        ) < 0.25 else str(rng.randint(2015, now.year))

        users_rows.append(
            (
                guid,
                1,
                consent_ts,
                rng.choice(["1.0.0", "1.1.0", "1.2.0", "2.0.0"]),
                rng.choice(GENDERS),
                rng.choice(AGE_GROUPS),
                _random_languages(rng, NATIVE_LANGUAGE_POOL,
                                  min_count=1, max_count=2),
                _random_languages(rng, OTHER_LANGUAGE_POOL,
                                  min_count=0, max_count=3),
                moved_to_finland,
                rng.choice(FINNISH_LEARNING_DURATION),
                cefr_level,
                _to_sql_timestamp(created_at),
            )
        )

        history_count = rng.randint(1, args.history_max)
        history_anchor = created_at
        for idx in range(history_count):
            change_dt = history_anchor + \
                timedelta(days=idx * rng.randint(20, 120))
            history_rows.append(
                (
                    guid,
                    cefr_level if idx == history_count -
                    1 else rng.choice(CEFR_LEVELS),
                    CEFR_HISTORY_SOURCE[idx % 2],
                    _to_sql_timestamp(change_dt),
                )
            )

        if rng.random() < args.request_rate:
            req_created = _random_timestamp(rng, now, args.days_back)
            status = rng.choices(REQUEST_STATUS, weights=[
                                 50, 20, 10, 20], k=1)[0]
            processed = _to_sql_timestamp(
                req_created + timedelta(days=rng.randint(1, 30))) if status != "pending" else None
            request_rows.append(
                (
                    guid,
                    rng.choice(REQUEST_TYPES),
                    status,
                    _to_sql_timestamp(req_created),
                    processed,
                    "seeded request",
                )
            )

        assessment_count = rng.randint(
            args.min_assessments, args.max_assessments)
        for idx in range(assessment_count):
            ts = _random_timestamp_current_year(rng, now)
            proficiency = round(rng.uniform(0.0, 5.0), 1)
            assessments_rows.append(
                (
                    guid,
                    f"task-{rng.randint(1, 40)}",
                    str(uuid4()),
                    f"audio/{guid}/{idx}.wav",
                    f"synthetic transcript {idx}",
                    round(rng.uniform(0.0, 5.0), 1),
                    round(rng.uniform(0.0, 5.0), 1),
                    proficiency,
                    round(rng.uniform(0.0, 5.0), 1),
                    round(rng.uniform(0.0, 5.0), 1),
                    _to_sql_timestamp(ts),
                )
            )

    print(f"fixed_guid: {args.fixed_guid}")
    if args.fixed_guid:
        fixed_guid = args.fixed_guid
        fixed_cefr = rng.choice(CEFR_LEVELS)
        fixed_created_at = _random_timestamp(rng, now, args.days_back)
        fixed_consent_ts = fixed_created_at.isoformat(timespec="seconds")
        fixed_moved_to_finland = "before_2015" if rng.random() < 0.25 else str(
            rng.randint(2015, now.year)
        )

        users_rows.append(
            (
                fixed_guid,
                1,
                fixed_consent_ts,
                rng.choice(["1.0.0", "1.1.0", "1.2.0", "2.0.0"]),
                rng.choice(GENDERS),
                rng.choice(AGE_GROUPS),
                _random_languages(rng, NATIVE_LANGUAGE_POOL,
                                  min_count=1, max_count=2),
                _random_languages(rng, OTHER_LANGUAGE_POOL,
                                  min_count=0, max_count=3),
                fixed_moved_to_finland,
                rng.choice(FINNISH_LEARNING_DURATION),
                fixed_cefr,
                _to_sql_timestamp(fixed_created_at),
            )
        )
        user_ids.append(fixed_guid)

        for idx in range(args.fixed_assessments):
            ts = _random_timestamp_current_year(rng, now)
            assessments_rows.append(
                (
                    fixed_guid,
                    f"task-{rng.randint(1, 40)}",
                    str(uuid4()),
                    f"audio/{fixed_guid}/fixed-{idx}.wav",
                    f"fixed user synthetic transcript {idx}",
                    round(rng.uniform(0.0, 5.0), 1),
                    round(rng.uniform(0.0, 5.0), 1),
                    round(rng.uniform(0.0, 5.0), 1),
                    round(rng.uniform(0.0, 5.0), 1),
                    round(rng.uniform(0.0, 5.0), 1),
                    _to_sql_timestamp(ts),
                )
            )

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")

        _ensure_schema(conn)
        if args.truncate:
            _truncate_tables(conn)

        conn.executemany(
            """
            INSERT INTO users(
                guid, consent_accepted, consent_timestamp, app_version,
                gender, age_group, native_languages, other_languages,
                moved_to_finland, finnish_learning_duration, cefr_level, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            users_rows,
        )

        conn.executemany(
            """
            INSERT INTO assessments(
                guid, task_id, audio_id, audio_path, transcript,
                accuracy, fluency, proficiency, pronunciation, range_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            assessments_rows,
        )

        conn.executemany(
            """
            INSERT INTO user_cefr_history(guid, cefr_level, source, created_at)
            VALUES (?, ?, ?, ?)
            """,
            history_rows,
        )

        if request_rows:
            conn.executemany(
                """
                INSERT INTO user_requests(guid, type, status, created_at, processed_at, admin_notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                request_rows,
            )

        assessment_id_rows = conn.execute(
            "SELECT id, guid FROM assessments").fetchall()
        feedback_assessment_rows: list[tuple[str,
                                             int, str, int, str, str]] = []
        for assessment_id, assessment_guid in assessment_id_rows:
            if rng.random() < args.feedback_rate:
                feedback_assessment_rows.append(
                    (
                        assessment_guid,
                        int(assessment_id),
                        rng.choice(FEEDBACK_ASSESSMENT_TYPES),
                        rng.randint(1, 5),
                        "seeded feedback",
                        _to_sql_timestamp(_random_timestamp(
                            rng, now, args.days_back)),
                    )
                )

        feedback_experience_rows: list[tuple[str, str, int, str, str]] = []
        for guid in user_ids:
            if rng.random() < args.feedback_rate:
                feedback_experience_rows.append(
                    (
                        guid,
                        rng.choice(FEEDBACK_EXPERIENCE_TYPES),
                        rng.randint(1, 5),
                        "seeded feedback",
                        _to_sql_timestamp(_random_timestamp(
                            rng, now, args.days_back)),
                    )
                )

        if feedback_assessment_rows:
            conn.executemany(
                """
                INSERT INTO feedback_assessment(guid, assessment_id, type, reaction_value, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                feedback_assessment_rows,
            )

        if feedback_experience_rows:
            conn.executemany(
                """
                INSERT INTO feedback_experience(guid, type, reaction_value, comment, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                feedback_experience_rows,
            )

        conn.commit()

    print("Seed complete")
    print(f"  db: {db_path}")
    print(f"  users: {len(users_rows)}")
    print(f"  assessments: {len(assessments_rows)}")
    print(f"  user_cefr_history: {len(history_rows)}")
    print(f"  user_requests: {len(request_rows)}")
    print(f"  feedback_assessment: {len(feedback_assessment_rows)}")
    print(f"  feedback_experience: {len(feedback_experience_rows)}")


if __name__ == "__main__":
    main()
