#!/usr/bin/env python3
"""Seed the DigiTala SQLite database with large synthetic datasets.

Usage examples:
  python3 scripts/seed_db.py --truncate
  python3 scripts/seed_db.py --users 20000 --min-assessments 2 --max-assessments 10 --truncate
  python3 scripts/seed_db.py --db development.db --seed 1234
  python3 scripts/seed_db.py --users 0 --fixed-guid 00000000-0000-0000-0000-000000000001 --fixed-assessments 10
  python3 scripts/seed_db.py --db development.db --users 1000 --feedback-rate 0.2 --request-rate 0.05
  python3 scripts/seed_db.py --db development.db --event-rate 1.0
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
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
# Dropped from the onboarding form in 2026-08, so later users leave them NULL.
BACKGROUND_FORM_DROPPED_AT = datetime(2026, 8, 1)
APP_VERSIONS = ["1.0.0", "1.1.0", "1.2.0", "2.0.0"]
EVENT_APP_VERSIONS = ["2.0.0", "2.1.0"]
FEEDBACK_ASSESSMENT_TYPES = [
    "self_assessment",
    "result_accuracy",
    "result_understanding",
]
FEEDBACK_EXPERIENCE_TYPES = ["comparison_ui", "overall_experience"]
REQUEST_TYPES = ["delete", "export"]
REQUEST_STATUS = ["pending", "approved", "denied", "completed"]
CONTENT_RELEVANCE = ["on_topic", "partial", "off_topic"]
# Matches Dimension in app/models/events.py.
DIMENSIONS = ["proficiency", "fluency",
              "pronunciation", "range", "accuracy"]

UserRow = tuple[str, int, str, str, str, str, str, str,
                str | None, str | None, str, str | None, str]
AssessmentRow = tuple[str, int, str, str, str, float, float, float, float, float,
                      str | None, float | None, str]
HistoryRow = tuple[str, str, str, str]
RequestRow = tuple[str, str, str, str, str | None, str | None]
FeedbackRow = tuple[str, int | None, str, int, str, str, str]
EventRow = tuple[str, str, str, str, str, str, str, str]


def _to_sql_timestamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _to_event_timestamp(dt: datetime) -> str:
    """`user_events.occurred_at` format: CURRENT_TIMESTAMP plus milliseconds."""

    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _random_timestamp(rng: random.Random, now: datetime, days_back: int) -> datetime:
    window_seconds = max(days_back, 1) * 24 * 60 * 60
    return now - timedelta(seconds=rng.randint(0, window_seconds))


def _random_timestamp_between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    window_seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, max(window_seconds, 0)))


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
    parser.add_argument("--event-rate", type=float, default=0.50,
                        help="Chance of a recorded ASA session per assessment (0-1)")
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
    if not 0 <= args.event_rate <= 1:
        raise ValueError("--event-rate must be between 0 and 1")
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
    conn.execute("DELETE FROM user_events")
    conn.execute("DELETE FROM feedback")
    conn.execute("DELETE FROM user_requests")
    conn.execute("DELETE FROM user_cefr_history")
    conn.execute("DELETE FROM assessments")
    conn.execute("DELETE FROM users")


def _build_cefr_history(  # pylint: disable=too-many-arguments
    rng: random.Random, guid: str, *, cefr_level: str, created_at: datetime,
    now: datetime, history_max: int,
) -> tuple[list[HistoryRow], str | None]:
    """Onboarding level first, then Advance/Revert steps of one level each.

    Returns the history rows and `current_cefr_level`, which stays NULL until the
    learner moves off their onboarding level, as in PATCH /users/level.
    """

    rows: list[HistoryRow] = [
        (guid, cefr_level, "self_report", _to_sql_timestamp(created_at))]
    level_idx = CEFR_LEVELS.index(cefr_level)
    changed_at = created_at
    for _ in range(rng.randint(1, history_max) - 1):
        changed_at += timedelta(days=rng.randint(20, 120))
        if changed_at > now:
            break
        step = rng.choice([-1, 1])
        level_idx = min(max(level_idx + step, 0), len(CEFR_LEVELS) - 1)
        rows.append((guid, CEFR_LEVELS[level_idx], "self_report",
                     _to_sql_timestamp(changed_at)))

    current = None if len(rows) == 1 else CEFR_LEVELS[level_idx]
    return rows, current


def _build_user(
    rng: random.Random, guid: str, cefr_level: str, created_at: datetime,
    current_cefr_level: str | None,
) -> UserRow:
    has_background_form = created_at < BACKGROUND_FORM_DROPPED_AT
    moved_to_finland = None
    finnish_learning_duration = None
    if has_background_form:
        moved_to_finland = "before_2015" if rng.random() < 0.25 else str(
            rng.randint(2015, created_at.year))
        finnish_learning_duration = rng.choice(FINNISH_LEARNING_DURATION)

    return (
        guid,
        1,
        created_at.isoformat(timespec="seconds"),
        rng.choice(APP_VERSIONS),
        rng.choice(GENDERS),
        rng.choice(AGE_GROUPS),
        _random_languages(rng, NATIVE_LANGUAGE_POOL, min_count=1, max_count=2),
        _random_languages(rng, OTHER_LANGUAGE_POOL, min_count=0, max_count=3),
        moved_to_finland,
        finnish_learning_duration,
        cefr_level,
        current_cefr_level,
        _to_sql_timestamp(created_at),
    )


def _build_assessment(  # pylint: disable=too-many-arguments
    rng: random.Random, guid: str, *, audio_path: str, transcript: str,
    created_at: datetime, now: datetime,
) -> AssessmentRow:
    # Assessments are recent, so analytics for the current year have data.
    start_of_year = datetime(now.year, 1, 1)
    ts = _random_timestamp_between(rng, max(created_at, start_of_year), now)

    content_relevance = None
    content_confidence = None
    # NULL means the relevance check did not run.
    if rng.random() < 0.85:
        content_relevance = rng.choices(
            CONTENT_RELEVANCE, weights=[80, 15, 5], k=1)[0]
        content_confidence = round(rng.uniform(0.5, 1.0), 2)

    return (
        guid,
        rng.randint(1, 5),
        str(uuid4()),
        audio_path,
        transcript,
        round(rng.uniform(0.0, 6.0), 1),
        round(rng.uniform(0.0, 6.0), 1),
        round(rng.uniform(0.0, 6.0), 1),
        round(rng.uniform(0.0, 6.0), 1),
        round(rng.uniform(0.0, 6.0), 1),
        content_relevance,
        content_confidence,
        _to_sql_timestamp(ts),
    )


def _build_feedback(  # pylint: disable=too-many-arguments
    rng: random.Random, guid: str, *, assessment_id: int | None, feedback_type: str,
    created_at: datetime, now: datetime,
) -> FeedbackRow:
    # Some learners change their answer, which moves updated_at past created_at.
    updated_at = created_at
    if rng.random() < 0.2:
        updated_at = min(
            created_at + timedelta(seconds=rng.randint(5, 600)), now)

    return (
        guid,
        assessment_id,
        feedback_type,
        rng.randint(1, 5),
        "seeded feedback",
        _to_sql_timestamp(created_at),
        _to_sql_timestamp(updated_at),
    )


def _build_session_events(
    rng: random.Random, guid: str, assessment_id: int, task_id: int, assessed_at: datetime,
) -> list[EventRow]:
    """One ASA session around an assessment, following docs/USER_EVENTS.md.

    Properties follow app/models/events.py, so the rows read as if POST /events stored them.
    """

    asa_session_id = str(uuid4())
    app_version = rng.choice(EVENT_APP_VERSIONS)
    # Start shortly before the recording the session produced.
    clock = assessed_at - timedelta(seconds=rng.randint(20, 90))
    rows: list[EventRow] = []

    def emit(name: str, properties: dict[str, Any], min_gap: float = 0.3,
             max_gap: float = 4.0) -> None:
        nonlocal clock
        clock += timedelta(seconds=rng.uniform(min_gap, max_gap))
        received_at = clock + timedelta(seconds=rng.uniform(1, 30))
        rows.append((
            guid,
            str(uuid4()),
            asa_session_id,
            name,
            json.dumps(properties, sort_keys=True, separators=(",", ":")),
            app_version,
            _to_event_timestamp(clock),
            _to_sql_timestamp(received_at),
        ))

    task = {"task_id": task_id}
    feedback = {**task, "assessment_id": assessment_id}

    emit("asa_session_started", {})
    # Task Selection stays open under every other screen until the quit.
    emit("task_selection_panel_toggled", {"active": True})

    if rng.random() < 0.3:
        emit("profile_panel_toggled", {"active": True}, 2, 15)
        for _ in range(rng.randint(0, 2)):
            emit("profile_panel_scrolled",
                 {"depth": round(rng.random(), 3)}, 1, 10)
        emit("profile_panel_toggled", {"active": False}, 2, 30)

    emit("task_panel_toggled", {"active": True, **task}, 2, 15)
    for take in range(rng.choices([1, 2], weights=[80, 20], k=1)[0]):
        emit("mic_button_clicked", {"button_id": "record", **task}, 1 if take else 2, 8)
        emit("mic_button_clicked", {"button_id": "stop", **task}, 10, 60)
        if rng.random() < 0.4:
            emit("mic_button_clicked", {"button_id": "replay", **task})

    emit("loading_modal_toggled", {"active": True, **task})
    emit("loading_modal_toggled", {"active": False, **task}, 2, 12)
    emit("feedback_panel_toggled", {"active": True, **feedback})
    if rng.random() < 0.3:
        emit("info_modal_toggled", {"active": True, **task})
        emit("info_modal_toggled", {"active": False, **task}, 2, 20)
    for dimension in rng.sample(DIMENSIONS, k=rng.randint(0, 3)):
        emit("dimension_modal_toggled",
             {"active": True, **task, "dimension": dimension})
        emit("dimension_modal_toggled",
             {"active": False, **task, "dimension": dimension}, 2, 25)
    for _ in range(rng.randint(0, 3)):
        emit("feedback_panel_scrolled",
             {"depth": round(rng.random(), 3), **task}, 1, 10)
    emit("feedback_panel_toggled", {"active": False, **feedback}, 3, 40)
    # 'new_task' returns to Task Selection, closing the task.
    emit("task_panel_toggled", {"active": False, **task})

    # Quitting closes what is still open, then ends the session. A killed app loses
    # both, which user_event_durations reports as a NULL duration.
    if rng.random() < 0.9:
        emit("task_selection_panel_toggled", {"active": False}, 1, 20)
        emit("asa_session_ended", {"reason": "quit"}, 0, 0.05)

    return rows


def main() -> None:
    """Main function to seed the database with synthetic data based on provided arguments."""

    # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    args = _parse_args()
    _validate_args(args)
    rng = random.Random(args.seed)
    now = datetime.now()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    users_rows: list[UserRow] = []
    assessments_rows: list[AssessmentRow] = []
    history_rows: list[HistoryRow] = []
    request_rows: list[RequestRow] = []
    user_created_at: dict[str, datetime] = {}

    for _ in range(args.users):
        guid = str(uuid4())
        cefr_level = rng.choices(CEFR_LEVELS, weights=[
                                 18, 24, 30, 20, 8], k=1)[0]
        created_at = _random_timestamp(rng, now, args.days_back)
        user_created_at[guid] = created_at

        user_history, current_cefr_level = _build_cefr_history(
            rng, guid, cefr_level=cefr_level, created_at=created_at, now=now,
            history_max=args.history_max)
        history_rows.extend(user_history)
        users_rows.append(_build_user(
            rng, guid, cefr_level, created_at, current_cefr_level))

        if rng.random() < args.request_rate:
            req_created = _random_timestamp_between(rng, created_at, now)
            status = rng.choices(REQUEST_STATUS, weights=[
                                 50, 20, 10, 20], k=1)[0]
            processed = _to_sql_timestamp(
                min(req_created + timedelta(days=rng.randint(1, 30)), now)
            ) if status != "pending" else None
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
            assessments_rows.append(_build_assessment(
                rng, guid, audio_path=f"audio/{guid}/{idx}.wav",
                transcript=f"synthetic transcript {idx}", created_at=created_at, now=now))

    print(f"fixed_guid: {args.fixed_guid}")
    if args.fixed_guid:
        fixed_guid = args.fixed_guid
        fixed_cefr = rng.choice(CEFR_LEVELS)
        fixed_created_at = _random_timestamp(rng, now, args.days_back)
        user_created_at[fixed_guid] = fixed_created_at

        fixed_history, fixed_current_cefr = _build_cefr_history(
            rng, fixed_guid, cefr_level=fixed_cefr, created_at=fixed_created_at, now=now,
            history_max=args.history_max)
        history_rows.extend(fixed_history)
        users_rows.append(_build_user(
            rng, fixed_guid, fixed_cefr, fixed_created_at, fixed_current_cefr))

        for idx in range(args.fixed_assessments):
            assessments_rows.append(_build_assessment(
                rng, fixed_guid, audio_path=f"audio/{fixed_guid}/fixed-{idx}.wav",
                transcript=f"fixed user synthetic transcript {idx}",
                created_at=fixed_created_at, now=now))

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
                moved_to_finland, finnish_learning_duration, cefr_level,
                current_cefr_level, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            users_rows,
        )

        conn.executemany(
            """
            INSERT INTO assessments(
                guid, task_id, audio_id, audio_path, transcript,
                accuracy, fluency, proficiency, pronunciation, range_score,
                content_relevance, content_confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        # Only the rows inserted by this run, so a run without --truncate links nothing
        # to existing assessments.
        audio_ids = {row[2] for row in assessments_rows}
        assessment_id_rows = [
            row for row in conn.execute(
                "SELECT id, guid, task_id, audio_id, created_at FROM assessments")
            if row[3] in audio_ids
        ]

        feedback_rows: list[FeedbackRow] = []
        event_rows: list[EventRow] = []
        for assessment_id, assessment_guid, task_id, _, created_at in assessment_id_rows:
            assessed_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            if rng.random() < args.feedback_rate:
                # One answer per question per recording (idx_feedback_one_answer_per_question).
                for feedback_type in rng.sample(
                        FEEDBACK_ASSESSMENT_TYPES, k=rng.randint(1, len(FEEDBACK_ASSESSMENT_TYPES))):
                    answered_at = min(
                        assessed_at + timedelta(seconds=rng.randint(10, 300)), now)
                    feedback_rows.append(_build_feedback(
                        rng, assessment_guid, assessment_id=int(assessment_id),
                        feedback_type=feedback_type, created_at=answered_at, now=now))
            if rng.random() < args.event_rate:
                event_rows.extend(_build_session_events(
                    rng, assessment_guid, int(assessment_id), int(task_id), assessed_at))

        # Experience feedback carries no assessment and is not deduplicated.
        for guid, created_at in user_created_at.items():
            if rng.random() < args.feedback_rate:
                feedback_rows.append(_build_feedback(
                    rng, guid, assessment_id=None,
                    feedback_type=rng.choice(FEEDBACK_EXPERIENCE_TYPES),
                    created_at=_random_timestamp_between(rng, created_at, now), now=now))

        if feedback_rows:
            conn.executemany(
                """
                INSERT INTO feedback(
                    guid, assessment_id, type, reaction_value, comment, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                feedback_rows,
            )

        if event_rows:
            conn.executemany(
                """
                INSERT INTO user_events(
                    guid, event_id, asa_session_id, name, properties, app_version,
                    occurred_at, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                event_rows,
            )

        conn.commit()

    print("Seed complete")
    print(f"  db: {db_path}")
    print(f"  users: {len(users_rows)}")
    print(f"  assessments: {len(assessments_rows)}")
    print(f"  user_cefr_history: {len(history_rows)}")
    print(f"  user_requests: {len(request_rows)}")
    print(f"  feedback: {len(feedback_rows)}")
    print(f"  user_events: {len(event_rows)}")


if __name__ == "__main__":
    main()
