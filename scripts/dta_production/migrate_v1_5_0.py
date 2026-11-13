"""In-place migration from the v1.4.0 schema to v1.5.0. No database recreation.

WHAT IT DOES

  user_events                         new table for POST /events, the in-app behaviour
                                      events. Erased with the user by the cascade.
  user_event_durations                new view pairing each *_toggled activation with its
                                      deactivation.
  idx_user_events_guid_occurred_at    new indexes on user_events.
  idx_user_events_name_occurred_at

WHY

  The app applies app/schema.sql only when the database file does not exist, so an
  existing database never gets `user_events`, and every POST /events fails with
  `no such table`. Every step here only adds something new: no existing table or row is
  touched.

Run with the stack STOPPED. Idempotent: each object is created only if it is missing, so
a second invocation is a no-op and a failed run can simply be repeated.

    python3 scripts/dta_production/migrate_v1_5_0.py --db /path/to/dta.db          # dry run
    python3 scripts/dta_production/migrate_v1_5_0.py --db /path/to/dta.db --apply
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

TABLES = ("users", "assessments", "feedback", "user_cefr_history", "user_requests")

# Copies of the matching blocks in app/schema.sql, without the comments, and without
# IF NOT EXISTS because _missing() decides what runs. In dependency order: the view and
# the indexes need the table. tests/test_migration_v1_5_0.py polices the duplication.
OBJECTS = (
    ("table", "user_events", """
        CREATE TABLE user_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          guid TEXT NOT NULL,
          event_id TEXT NOT NULL,
          asa_session_id TEXT NOT NULL,
          name TEXT NOT NULL,
          properties TEXT NOT NULL CHECK (
            json_valid (properties)
            AND json_type (properties) = 'object'
          ),
          app_version TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (guid, event_id),
          FOREIGN KEY (guid) REFERENCES users (guid) ON DELETE CASCADE
        )
    """),
    ("view", "user_event_durations", r"""
        CREATE VIEW user_event_durations AS
        WITH
          toggles AS (
            SELECT
              guid,
              asa_session_id,
              name,
              json_extract (properties, '$.task_id') AS task_id,
              json_extract (properties, '$.dimension') AS dimension,
              json_extract (properties, '$.assessment_id') AS assessment_id,
              json_extract (properties, '$.active') AS active,
              occurred_at,
              LEAD (json_extract (properties, '$.active')) OVER w AS next_active,
              LEAD (occurred_at) OVER w AS next_occurred_at
            FROM
              user_events
            WHERE
              name LIKE '%\_toggled' ESCAPE '\'
            WINDOW
              w AS (
                PARTITION BY
                  guid,
                  asa_session_id,
                  name,
                  json_extract (properties, '$.task_id'),
                  json_extract (properties, '$.dimension')
                ORDER BY
                  occurred_at,
                  id
              )
          )
        SELECT
          guid,
          asa_session_id,
          name,
          task_id,
          dimension,
          assessment_id,
          occurred_at AS opened_at,
          CASE
            WHEN next_active = 0 THEN next_occurred_at
          END AS closed_at,
          CASE
            WHEN next_active = 0 THEN CAST(
              ROUND(
                (julianday (next_occurred_at) - julianday (occurred_at)) * 86400000
              ) AS INTEGER
            )
          END AS duration_ms
        FROM
          toggles
        WHERE
          active = 1
    """),
    ("index", "idx_user_events_guid_occurred_at",
     "CREATE INDEX idx_user_events_guid_occurred_at ON user_events (guid, occurred_at)"),
    ("index", "idx_user_events_name_occurred_at",
     "CREATE INDEX idx_user_events_name_occurred_at ON user_events (name, occurred_at)"),
)


def _exists(db: sqlite3.Connection, kind: str, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?",
                      (kind, name)).fetchone() is not None


def _missing(db: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return [obj for obj in OBJECTS if not _exists(db, obj[0], obj[1])]


def _count(db: sqlite3.Connection, table: str) -> int:
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def plan(db: sqlite3.Connection) -> list[str]:
    """What this run would change, in the order it would happen."""

    return [f"CREATE {kind.upper()} {name}" for kind, name, _ in _missing(db)]


def migrate(db: sqlite3.Connection) -> None:
    """Apply every outstanding step in one transaction. Safe to run twice."""

    missing = _missing(db)
    if not missing:
        return

    # pysqlite does not open a transaction before DDL on its own, so without this
    # each CREATE would commit separately and a failure could leave half a migration.
    db.execute("BEGIN")
    try:
        for _, _, ddl in missing:
            db.execute(ddl)
    except Exception:
        db.rollback()
        raise
    db.commit()


def verify(db: sqlite3.Connection, before: dict[str, int]) -> list[str]:
    """Post-conditions. Returns a list of problems; empty means the migration held."""

    problems = [f"{kind} {name} is missing" for kind, name, _ in _missing(db)]
    if problems:
        return problems

    columns = {row[1] for row in db.execute("PRAGMA table_info(user_events)")}
    expected = {"id", "guid", "event_id", "asa_session_id", "name", "properties",
                "app_version", "occurred_at", "received_at"}
    if columns != expected:
        problems.append(f"user_events has the wrong columns: {sorted(columns)}")

    # Without the cascade, deleting a user would leave their events behind.
    cascades = [(row[2], row[6]) for row in db.execute(
        "PRAGMA foreign_key_list(user_events)")]
    if cascades != [("users", "CASCADE")]:
        problems.append(f"user_events foreign keys are wrong: {cascades}")

    # Existing data must be untouched.
    for table in TABLES:
        after = _count(db, table)
        if after != before[table]:
            problems.append(f"{table}: {before[table]} row(s) before, {after} after")

    # The view relies on window and JSON functions. Preparing a query against it fails
    # here, rather than on the first analytics query, if this SQLite lacks either.
    try:
        db.execute("SELECT * FROM user_event_durations LIMIT 0").fetchall()
    except sqlite3.Error as error:
        problems.append(f"user_event_durations cannot be queried: {error}")

    # A JSON array is valid JSON but not an object, so the CHECK must reject it. The
    # probe needs a real user to get past the foreign key.
    guid = db.execute("SELECT guid FROM users LIMIT 1").fetchone()
    if guid is not None:
        try:
            db.execute(
                "INSERT INTO user_events (guid, event_id, asa_session_id, name, "
                "properties, app_version, occurred_at) "
                "VALUES (?, 'probe', 'probe', 'probe', '[]', 'probe', 'probe')",
                guid,
            )
        except sqlite3.IntegrityError:
            db.rollback()
        else:
            db.rollback()
            problems.append("the properties CHECK constraint is missing")

    # Scoped to the new table: an orphan elsewhere predates this migration, and reporting
    # it here would fail a run that did everything right.
    if db.execute("PRAGMA foreign_key_check(user_events)").fetchall():
        problems.append("foreign key violations in user_events")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        problems.append("integrity_check failed")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="path to dta.db")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (otherwise this is a dry run)")
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the pre-migration copy (not recommended)")
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"no such database: {args.db}", file=sys.stderr)
        return 2

    with sqlite3.connect(args.db) as db:
        db.execute("PRAGMA foreign_keys = ON")
        steps = plan(db)
        before = {table: _count(db, table) for table in TABLES}

    print("before: " + ", ".join(f"{k}={v}" for k, v in before.items()))

    if not steps:
        print("nothing to do: the database is already on the v1.5.0 schema")
        return 0

    print("\nplanned:")
    for step in steps:
        print(f"  - {step}")

    if not args.apply:
        print("\ndry run. re-run with --apply to write these changes.")
        return 0

    if not args.no_backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.db.with_name(f"{args.db.name}.pre-v1.5.0.{stamp}")
        # sqlite3's own backup API rather than a file copy: it is consistent even if
        # something is mid-write, and it does not need the WAL to be checkpointed first.
        with sqlite3.connect(args.db) as src, sqlite3.connect(backup) as dst:
            src.backup(dst)
        print(f"\nbackup: {backup}")

    with sqlite3.connect(args.db) as db:
        db.execute("PRAGMA foreign_keys = ON")
        migrate(db)

    with sqlite3.connect(args.db) as db:
        db.execute("PRAGMA foreign_keys = ON")
        problems = verify(db, before)
        after = {table: _count(db, table) for table in TABLES}

    print("after:  " + ", ".join(f"{k}={v}" for k, v in after.items()))

    if problems:
        print("\nFAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("\nmigration complete and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
