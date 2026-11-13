# pylint: disable=redefined-outer-name

"""The v1.4.0 -> v1.5.0 in-place migration must land on exactly the v1.5.0 schema.

The migration script carries hand-copied DDL for `user_events`, its view and its indexes.
These tests are what stops that copy drifting from app/schema.sql.

tests/fixtures/schema_v1_4_0.sql is the released v1.4.0 schema, captured from the tag.
"""

import importlib.util
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dta_production" / "migrate_v1_5_0.py"
SCHEMA_V1_5_0 = REPO / "app" / "schema.sql"
SCHEMA_V1_4_0 = Path(__file__).parent / "fixtures" / "schema_v1_4_0.sql"


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("migrate_v1_5_0", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(path: Path, schema: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript(schema.read_text(encoding="utf-8"))
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _normalise(sql: str) -> str:
    """Strip comments, collapse whitespace and drop IF NOT EXISTS.

    SQLite stores each CREATE statement's text verbatim, comments included, so this is
    what makes the migration's comment-free copy comparable with app/schema.sql.
    """

    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"\s+", " ", sql).strip()
    return sql.replace("IF NOT EXISTS ", "")


def _schema(db: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """Every table, view and index, keyed by (type, name), with its normalised DDL."""

    return {
        (kind, name): _normalise(sql)
        for kind, name, sql in db.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")
    }


def _seed_user(db: sqlite3.Connection) -> str:
    guid = str(uuid4())
    db.execute(
        "INSERT INTO users (guid, consent_accepted, consent_timestamp, cefr_level, "
        "created_at) VALUES (?, 1, '2026-01-01T00:00:00Z', 'A2', '2026-01-01 00:00:00')",
        (guid,),
    )
    db.commit()
    return guid


def _insert_event(db: sqlite3.Connection, guid: str, event_id: str, name: str,
                  properties: str, occurred_at: str) -> None:
    db.execute(
        "INSERT INTO user_events (guid, event_id, asa_session_id, name, properties, "
        "app_version, occurred_at) VALUES (?, ?, 's1', ?, ?, '1.0.0', ?)",
        (guid, event_id, name, properties, occurred_at),
    )


def test_migrated_schema_matches_a_fresh_one(migration, tmp_path):
    """The whole point: a migrated database and a newly created one are the same shape.

    Compares the stored DDL of every object, so a drifted CHECK, column default or view
    body fails here, not only a missing column.
    """

    old = _build(tmp_path / "old.db", SCHEMA_V1_4_0)
    _seed_user(old)
    migration.migrate(old)

    new = _build(tmp_path / "new.db", SCHEMA_V1_5_0)

    assert _schema(old) == _schema(new)


def test_plan_lists_every_new_object_in_order(migration, tmp_path):
    """The table comes first, because the view and the indexes depend on it."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_4_0)

    assert migration.plan(db) == [
        "CREATE TABLE user_events",
        "CREATE VIEW user_event_durations",
        "CREATE INDEX idx_user_events_guid_occurred_at",
        "CREATE INDEX idx_user_events_name_occurred_at",
    ]


def test_existing_rows_survive_and_verify_passes(migration, tmp_path):
    db = _build(tmp_path / "old.db", SCHEMA_V1_4_0)
    guid = _seed_user(db)
    db.execute("INSERT INTO user_requests (guid, type) VALUES (?, 'delete')", (guid,))
    db.commit()
    before = {table: migration._count(db, table)  # pylint: disable=protected-access
              for table in migration.TABLES}

    migration.migrate(db)

    assert migration.verify(db, before) == []
    assert db.execute("SELECT guid FROM users").fetchall() == [(guid,)]
    assert db.execute("SELECT guid FROM user_requests").fetchall() == [(guid,)]


def test_an_unrelated_orphan_does_not_fail_verification(migration, tmp_path):
    """A row orphaned before the migration is not this migration's fault.

    Any connection that forgets `PRAGMA foreign_keys = ON` can leave one behind.
    """

    db = _build(tmp_path / "old.db", SCHEMA_V1_4_0)
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("INSERT INTO user_cefr_history (guid, cefr_level, source) "
               "VALUES ('ghost', 'A1', 'model')")
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    before = {table: migration._count(db, table)  # pylint: disable=protected-access
              for table in migration.TABLES}

    migration.migrate(db)

    assert migration.verify(db, before) == []


def test_a_failed_run_leaves_nothing_behind(migration, tmp_path, monkeypatch):
    """The steps share one transaction, so a failure part-way rolls all of them back."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_4_0)
    broken = ("index", "idx_broken", "CREATE INDEX idx_broken ON user_events (nope)")
    monkeypatch.setattr(migration, "OBJECTS", migration.OBJECTS[:3] + (broken,))

    with pytest.raises(sqlite3.OperationalError):
        migration.migrate(db)

    assert len(migration.plan(db)) == 4


def test_events_behave_as_in_a_fresh_database(migration, tmp_path):
    """Dedup, the CHECK, the view and the cascade all work on a migrated database."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_4_0)
    guid = _seed_user(db)
    migration.migrate(db)

    _insert_event(db, guid, "e1", "transcript_toggled", '{"active": true}',
                  "2026-01-01 10:00:00.000")
    _insert_event(db, guid, "e2", "transcript_toggled", '{"active": false}',
                  "2026-01-01 10:00:02.500")
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(db, guid, "e1", "transcript_toggled", '{"active": true}',
                      "2026-01-01 10:00:00.000")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(db, guid, "e3", "transcript_toggled", "[]",
                      "2026-01-01 10:00:00.000")
    db.rollback()

    assert db.execute(
        "SELECT name, duration_ms FROM user_event_durations").fetchall() == [
            ("transcript_toggled", 2500)]

    db.execute("DELETE FROM users WHERE guid = ?", (guid,))
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM user_events").fetchone()[0] == 0


def test_a_partial_run_is_completed(migration, tmp_path):
    """A run interrupted after the table was created picks up the rest."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_4_0)
    _, _, table_ddl = migration.OBJECTS[0]
    db.execute(table_ddl)
    db.commit()

    assert migration.plan(db) == [
        "CREATE VIEW user_event_durations",
        "CREATE INDEX idx_user_events_guid_occurred_at",
        "CREATE INDEX idx_user_events_name_occurred_at",
    ]

    migration.migrate(db)

    assert migration.plan(db) == []
    assert _schema(db) == _schema(_build(tmp_path / "new.db", SCHEMA_V1_5_0))


def test_migration_is_idempotent(migration, tmp_path):
    """A second run is a no-op, so a failed run can simply be repeated."""

    db = _build(tmp_path / "old.db", SCHEMA_V1_4_0)
    guid = _seed_user(db)
    migration.migrate(db)
    _insert_event(db, guid, "e1", "app_opened", "{}", "2026-01-01 10:00:00.000")
    db.commit()
    first = _schema(db)

    assert migration.plan(db) == []
    migration.migrate(db)

    assert _schema(db) == first
    assert db.execute("SELECT COUNT(*) FROM user_events").fetchone()[0] == 1


def test_cli_dry_run_then_apply(migration, tmp_path, monkeypatch, capsys):
    """The dry run writes nothing; --apply backs up, migrates and verifies."""

    path = tmp_path / "dta.db"
    db = _build(path, SCHEMA_V1_4_0)
    _seed_user(db)
    db.close()

    monkeypatch.setattr("sys.argv", ["migrate_v1_5_0.py", "--db", str(path)])
    assert migration.main() == 0
    assert "dry run" in capsys.readouterr().out
    with sqlite3.connect(path) as db:
        assert len(migration.plan(db)) == 4

    monkeypatch.setattr("sys.argv", ["migrate_v1_5_0.py", "--db", str(path), "--apply"])
    assert migration.main() == 0
    assert "migration complete and verified" in capsys.readouterr().out
    assert len(list(tmp_path.glob("dta.db.pre-v1.5.0.*"))) == 1

    assert migration.main() == 0
    assert "nothing to do" in capsys.readouterr().out
