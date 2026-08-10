# pylint: disable=redefined-outer-name

"""The v1.2.0 -> v1.3.0 in-place migration must land on exactly the v1.3.0 schema.

The migration script carries a hand-copied `feedback` DDL (SQLite cannot add a NOT NULL
DEFAULT CURRENT_TIMESTAMP column to a populated table, so that table is rebuilt rather than
altered). These tests are what stops that copy drifting from app/schema.sql: a change to
one without the other fails here rather than in production, where the symptom would be new
feedback rows silently storing a NULL timestamp.

tests/fixtures/schema_v1_2_0.sql is the released v1.2.0 schema, captured from the tag.
"""

import importlib.util
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

REPO = Path(__file__).resolve().parents[1]
SCHEMA_V1_3_0 = REPO / "app" / "schema.sql"
SCHEMA_V1_2_0 = Path(__file__).parent / "fixtures" / "schema_v1_2_0.sql"


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location(
        "migrate_v1_3_0", REPO / "scripts" / "dta_production" / "migrate_v1_3_0.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(path: Path, schema: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript(schema.read_text(encoding="utf-8"))
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _shape(db: sqlite3.Connection, table: str) -> dict:
    """Column name -> (type, notnull, default), plus the table's index names.

    Compared as a mapping rather than a list because ALTER TABLE ADD COLUMN appends to the
    end while schema.sql declares `current_cefr_level` before `created_at`. Column ORDER is
    not observable through any query this app makes -- there is no `SELECT *` in app/ --
    so the order difference is cosmetic and deliberately not asserted.
    """

    return {
        "columns": {
            row[1]: (row[2], row[3], row[4])
            for row in db.execute(f"PRAGMA table_info({table})")
        },
        "indexes": {row[1] for row in db.execute(f"PRAGMA index_list({table})")},
    }


def _seed(db: sqlite3.Connection, *, duplicates: bool = True) -> str:
    guid = str(uuid4())
    db.execute(
        "INSERT INTO users (guid, consent_accepted, consent_timestamp, cefr_level, "
        "created_at) VALUES (?, 1, '2026-01-01T00:00:00Z', 'A2', '2026-01-01 00:00:00')",
        (guid,),
    )
    db.execute(
        "INSERT INTO assessments (id, guid, task_id, audio_id, audio_path, proficiency) "
        "VALUES (1, ?, 1, ?, '/dev/null', 2.1)",
        (guid, str(uuid4())),
    )
    rows = [(guid, 1, "result_accuracy", 2, "first", "2026-01-01 00:00:00")]
    if duplicates:
        rows += [
            (guid, 1, "result_accuracy", 4, "changed", "2026-01-02 00:00:00"),
            (guid, 1, "result_accuracy", 5, "final", "2026-01-03 00:00:00"),
        ]
    # An app-scoped answer, which must survive un-deduplicated.
    rows += [(guid, None, "overall_experience", 3, None, "2026-01-01 00:00:00"),
             (guid, None, "overall_experience", 4, None, "2026-01-02 00:00:00")]
    db.executemany(
        "INSERT INTO feedback (guid, assessment_id, type, reaction_value, comment, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()
    return guid


def test_migrated_schema_matches_a_fresh_one(migration, tmp_path):
    """The whole point: a migrated database and a newly created one are the same shape."""

    old = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    _seed(old)
    migration.migrate(old)
    old.commit()

    new = _build(tmp_path / "new.db", SCHEMA_V1_3_0)

    for table in ("users", "assessments", "feedback", "user_cefr_history",
                  "user_requests"):
        assert _shape(old, table) == _shape(new, table), f"{table} drifted"


def test_updated_at_is_not_null_and_defaults_after_migration(migration, tmp_path):
    """The bug this rebuild exists to prevent.

    `create_feedback` does not name `updated_at` in its INSERT. If the migration left the
    column nullable and without a default, every new row on the live database would store
    NULL while a fresh one stored a timestamp.
    """

    db = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    guid = _seed(db)
    migration.migrate(db)

    columns = _shape(db, "feedback")["columns"]
    assert columns["updated_at"][1] == 1, "updated_at must be NOT NULL"
    assert columns["updated_at"][2] == "CURRENT_TIMESTAMP"

    db.execute(
        "INSERT INTO feedback (guid, assessment_id, type, reaction_value) "
        "VALUES (?, NULL, 'comparison_ui', 3)", (guid,))

    assert db.execute(
        "SELECT COUNT(*) FROM feedback WHERE updated_at IS NULL").fetchone()[0] == 0


def test_duplicates_collapse_to_the_newest_answer(migration, tmp_path):
    """The newest row is the learner's final answer; earlier ones are keystrokes."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    guid = _seed(db)
    migration.migrate(db)

    rows = db.execute(
        "SELECT reaction_value, comment FROM feedback "
        "WHERE guid = ? AND type = 'result_accuracy'", (guid,)).fetchall()

    assert rows == [(5, "final")]


def test_app_scoped_answers_are_not_deduplicated(migration, tmp_path):
    """`overall_experience` carries no assessment_id and is outside the unique index."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    guid = _seed(db)
    migration.migrate(db)

    assert db.execute(
        "SELECT COUNT(*) FROM feedback WHERE guid = ? AND type = 'overall_experience'",
        (guid,)).fetchone()[0] == 2


def test_existing_rows_keep_their_own_timestamps(migration, tmp_path):
    """Nobody edited these rows at migration time, so updated_at inherits created_at."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    _seed(db)
    migration.migrate(db)

    assert db.execute(
        "SELECT COUNT(*) FROM feedback WHERE updated_at <> created_at").fetchone()[0] == 0


def test_self_assessment_survives_and_history_is_backfilled(migration, tmp_path):
    """current_cefr_level starts NULL -- "never moved" -- and the trail gets a start."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    guid = _seed(db)
    migration.migrate(db)

    level, current = db.execute(
        "SELECT cefr_level, current_cefr_level FROM users WHERE guid = ?",
        (guid,)).fetchone()
    assert (level, current) == ("A2", None)

    history = db.execute(
        "SELECT cefr_level, source, created_at FROM user_cefr_history WHERE guid = ?",
        (guid,)).fetchall()
    # Dated from the user row, not from migration time.
    assert history == [("A2", "self_report", "2026-01-01 00:00:00")]


def test_migration_is_idempotent(migration, tmp_path):
    db = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    _seed(db)

    migration.migrate(db)
    first = _shape(db, "feedback"), db.execute(
        "SELECT COUNT(*) FROM feedback").fetchone()[0]

    assert migration.plan(db) == []
    migration.migrate(db)

    assert (_shape(db, "feedback"),
            db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]) == first
    assert migration.verify(db) == []


def test_verify_passes_on_a_migrated_database(migration, tmp_path):
    db = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    _seed(db)
    migration.migrate(db)

    assert migration.verify(db) == []


def test_assessments_and_audio_paths_are_untouched(migration, tmp_path):
    """The reason this is a migration and not a recreate: collected data survives."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_2_0)
    _seed(db)
    before = db.execute(
        "SELECT id, guid, task_id, audio_path, proficiency FROM assessments").fetchall()

    migration.migrate(db)

    assert db.execute(
        "SELECT id, guid, task_id, audio_path, proficiency FROM assessments"
    ).fetchall() == before
