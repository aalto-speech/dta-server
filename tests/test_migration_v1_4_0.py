# pylint: disable=redefined-outer-name

"""The v1.3.0 -> v1.4.0 in-place migration must land on exactly the v1.4.0 schema.

The migration script carries a hand-copied `user_requests` DDL (SQLite cannot drop a
constraint in place, so the table is rebuilt rather than altered). These tests are what
stops that copy drifting from app/schema.sql.

tests/fixtures/schema_v1_3_0.sql is the released v1.3.0 schema, captured from the tag.

The behavioural test at the bottom is the point of the whole migration: a deletion record
has to outlive the user it records.
"""

import importlib.util
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

REPO = Path(__file__).resolve().parents[1]
SCHEMA_V1_4_0 = REPO / "app" / "schema.sql"
SCHEMA_V1_3_0 = Path(__file__).parent / "fixtures" / "schema_v1_3_0.sql"


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location(
        "migrate_v1_4_0", REPO / "scripts" / "dta_production" / "migrate_v1_4_0.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(path: Path, schema: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript(schema.read_text(encoding="utf-8"))
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _shape(db: sqlite3.Connection, table: str) -> dict:
    """Column name -> (type, notnull, default), plus the table's index names."""

    return {
        "columns": {
            row[1]: (row[2], row[3], row[4])
            for row in db.execute(f"PRAGMA table_info({table})")
        },
        "indexes": {row[1] for row in db.execute(f"PRAGMA index_list({table})")},
    }


def _foreign_keys(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[2] for row in db.execute(f"PRAGMA foreign_key_list({table})")}


def _seed_user(db: sqlite3.Connection) -> str:
    guid = str(uuid4())
    db.execute(
        "INSERT INTO users (guid, consent_accepted, consent_timestamp, cefr_level, "
        "created_at) VALUES (?, 1, '2026-01-01T00:00:00Z', 'A2', '2026-01-01 00:00:00')",
        (guid,),
    )
    db.commit()
    return guid


def test_migrated_schema_matches_a_fresh_one(migration, tmp_path):
    """The whole point: a migrated database and a newly created one are the same shape."""

    old = _build(tmp_path / "old.db", SCHEMA_V1_3_0)
    _seed_user(old)
    migration.migrate(old)
    old.commit()

    new = _build(tmp_path / "new.db", SCHEMA_V1_4_0)

    for table in ("users", "assessments", "feedback", "user_cefr_history",
                  "user_requests"):
        assert _shape(old, table) == _shape(new, table), f"{table} drifted"


def test_the_foreign_key_to_users_is_gone(migration, tmp_path):
    """The constraint this migration exists to remove, in both databases."""

    old = _build(tmp_path / "old.db", SCHEMA_V1_3_0)
    assert "users" in _foreign_keys(old, "user_requests"), "fixture is not the v1.3.0 shape"

    migration.migrate(old)

    assert _foreign_keys(old, "user_requests") == set()
    assert _foreign_keys(_build(tmp_path / "new.db", SCHEMA_V1_4_0), "user_requests") == set()


def test_existing_rows_survive_the_rebuild(migration, tmp_path):
    """A rebuild that loses the rows it was protecting would be worse than no migration."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_3_0)
    guid = _seed_user(db)
    db.execute(
        "INSERT INTO user_requests (id, guid, type, status, created_at, processed_at, "
        "admin_notes) VALUES (7, ?, 'delete', 'pending', '2026-01-01 00:00:00', NULL, "
        "'left over from a failed erase')",
        (guid,),
    )
    db.commit()

    migration.migrate(db)

    assert db.execute(
        "SELECT id, guid, type, status, created_at, processed_at, admin_notes "
        "FROM user_requests").fetchall() == [
            (7, guid, "delete", "pending", "2026-01-01 00:00:00", None,
             "left over from a failed erase")]

    # The counter has to resume past the copied id, not hand it out a second time.
    db.execute("INSERT INTO user_requests (guid, type) VALUES (?, 'delete')", (guid,))
    assert db.execute("SELECT MAX(id) FROM user_requests").fetchone()[0] == 8


def test_a_deletion_record_outlives_the_user(migration, tmp_path):
    """The behaviour the whole change exists for.

    Before the migration the cascade erased the record along with the user, which is why
    a completed deletion could never be recorded. After it, the row is what remains.
    """

    db = _build(tmp_path / "old.db", SCHEMA_V1_3_0)
    guid = _seed_user(db)

    db.execute("INSERT INTO user_requests (guid, type, status) "
               "VALUES (?, 'delete', 'completed')", (guid,))
    db.execute("DELETE FROM users WHERE guid = ?", (guid,))
    assert db.execute("SELECT COUNT(*) FROM user_requests").fetchone()[0] == 0, \
        "the v1.3.0 cascade should destroy the record -- that is the bug being fixed"

    migration.migrate(db)

    guid = _seed_user(db)
    db.execute("INSERT INTO user_requests (guid, type, status) "
               "VALUES (?, 'delete', 'completed')", (guid,))
    db.execute("DELETE FROM users WHERE guid = ?", (guid,))
    db.commit()

    assert db.execute(
        "SELECT guid FROM user_requests WHERE status = 'completed'").fetchall() == [(guid,)]
    assert db.execute("SELECT COUNT(*) FROM users WHERE guid = ?", (guid,)).fetchone()[0] == 0


def test_recording_a_deletion_for_a_vanished_user_is_possible(migration, tmp_path):
    """The other half of what the foreign key broke.

    Recording a deletion for a guid that is no longer in `users` raised IntegrityError
    under v1.3.0 -- so the failure-path audit trail could not be written in exactly the
    case that needed it, a retried deletion whose user row was already gone.
    """

    db = _build(tmp_path / "old.db", SCHEMA_V1_3_0)
    vanished = str(uuid4())

    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO user_requests (guid, type) VALUES (?, 'delete')",
                   (vanished,))
    db.rollback()

    migration.migrate(db)

    db.execute("INSERT INTO user_requests (guid, type) VALUES (?, 'delete')", (vanished,))
    db.commit()
    assert db.execute("SELECT guid FROM user_requests").fetchall() == [(vanished,)]


def test_migration_is_idempotent(migration, tmp_path):
    """A second run is a no-op, so a failed run can simply be repeated."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_3_0)
    guid = _seed_user(db)
    db.execute("INSERT INTO user_requests (guid, type) VALUES (?, 'delete')", (guid,))
    db.commit()

    migration.migrate(db)
    first = _shape(db, "user_requests")
    assert migration.plan(db) == []

    migration.migrate(db)

    assert _shape(db, "user_requests") == first
    assert db.execute("SELECT COUNT(*) FROM user_requests").fetchone()[0] == 1
