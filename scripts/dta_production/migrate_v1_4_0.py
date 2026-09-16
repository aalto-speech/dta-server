"""In-place migration from the v1.3.0 schema to v1.4.0. No database recreation.

WHAT IT DOES

  user_requests   rebuilt WITHOUT its FOREIGN KEY to users. Every row is carried across.

WHY

  The foreign key was `ON DELETE CASCADE`, which made the table unable to do the one job
  it now has. From v1.4.0 a completed deletion writes a row here holding the guid, the
  time and the number of recordings removed -- the record that says which GUIDs in an
  already-downloaded archive copy may no longer be used. Under the old schema that row
  was destroyed by the very `DELETE FROM users` it was recording.

  The same constraint also blocked the INSERT: recording a deletion for a guid that is no
  longer in `users` raised `FOREIGN KEY constraint failed`, which the service swallowed
  into a log line. So the failure-path audit trail that has been in place since v1.3.0
  could not be written in the case it most needed to be -- a retried deletion.

  Nothing is lost by dropping it. Nothing references `user_requests`, and an unmatched row
  here is a typo, while a missing one is a deletion nobody can prove happened.

Run with the stack STOPPED. Idempotent: the rebuild checks whether it has already run, so
a second invocation is a no-op and a failed run can simply be repeated.

    python3 scripts/dta_production/migrate_v1_4_0.py --db /path/to/dta.db          # dry run
    python3 scripts/dta_production/migrate_v1_4_0.py --db /path/to/dta.db --apply
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

TABLES = ("users", "assessments", "feedback", "user_cefr_history", "user_requests")


def _references_users(db: sqlite3.Connection) -> bool:
    """Whether user_requests still carries the foreign key this migration removes."""

    return any(row[2] == "users"
               for row in db.execute("PRAGMA foreign_key_list(user_requests)"))


def _count(db: sqlite3.Connection, table: str) -> int:
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def plan(db: sqlite3.Connection) -> list[str]:
    """What this run would change, in the order it would happen."""

    if not _references_users(db):
        return []

    rows = _count(db, "user_requests")
    return [f"user_requests: rebuild without the foreign key to users "
            f"({rows} row(s) carried across)"]


def _rebuild_user_requests(db: sqlite3.Connection) -> None:
    """Replace `user_requests` with the v1.4.0 shape, carrying every row across.

    A rebuild rather than an ALTER, because SQLite cannot drop a constraint in place.

    `id` values are copied explicitly so the AUTOINCREMENT counter resumes where it left
    off rather than reusing an id that an export or a log line already refers to.

    The DDL below is a copy of the `user_requests` block in app/schema.sql. That
    duplication is what tests/test_migration_v1_4_0.py exists to police.
    """

    # Step 1 of SQLite's documented table-rebuild procedure. It has to happen OUTSIDE a
    # transaction: `PRAGMA foreign_keys` is a silent no-op while one is open, and pysqlite
    # opens one implicitly on the first write. Committing first guarantees there is none,
    # and the assert is here because the failure mode is silence rather than an error --
    # migrate_v1_3_0.py:107 issues the same pragma after a write and never gets it.
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0, \
        "foreign_keys did not turn off; a transaction is still open"

    try:
        db.execute(
            """
            CREATE TABLE user_requests_v1_4_0 (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              guid TEXT NOT NULL,
              type TEXT NOT NULL CHECK (type IN ('delete', 'export')),
              status TEXT NOT NULL DEFAULT 'pending' CHECK (
                status IN ('pending', 'approved', 'denied', 'completed')
              ),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              processed_at TEXT,
              admin_notes TEXT
            )
            """
        )
        db.execute(
            """
            INSERT INTO user_requests_v1_4_0 (
              id, guid, type, status, created_at, processed_at, admin_notes
            )
            SELECT id, guid, type, status, created_at, processed_at, admin_notes
            FROM user_requests
            """
        )
        db.execute("DROP TABLE user_requests")
        db.execute("ALTER TABLE user_requests_v1_4_0 RENAME TO user_requests")
        db.commit()
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def migrate(db: sqlite3.Connection) -> None:
    """Apply every outstanding step. Safe to run twice."""

    if _references_users(db):
        _rebuild_user_requests(db)


def verify(db: sqlite3.Connection, before: dict[str, int]) -> list[str]:
    """Post-conditions. Returns a list of problems; empty means the migration held."""

    problems = []

    if _references_users(db):
        problems.append("user_requests still references users")

    columns = {row[1] for row in db.execute("PRAGMA table_info(user_requests)")}
    expected = {"id", "guid", "type", "status", "created_at", "processed_at",
                "admin_notes"}
    if columns != expected:
        problems.append(f"user_requests has the wrong columns: {sorted(columns)}")

    # The rebuild must not have cost a row, here or anywhere else.
    for table in TABLES:
        after = _count(db, table)
        if after != before[table]:
            problems.append(f"{table}: {before[table]} row(s) before, {after} after")

    # The constraint has to survive the rebuild, or a later typo lands unnoticed.
    try:
        db.execute("INSERT INTO user_requests (guid, type) VALUES ('probe', 'nonsense')")
    except sqlite3.IntegrityError:
        db.rollback()
    else:
        db.rollback()
        problems.append("the type CHECK constraint was lost in the rebuild")

    if db.execute("PRAGMA foreign_key_check").fetchall():
        problems.append("foreign key violations after migration")
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
        print("nothing to do: the database is already on the v1.4.0 schema")
        return 0

    print("\nplanned:")
    for step in steps:
        print(f"  - {step}")

    if not args.apply:
        print("\ndry run. re-run with --apply to write these changes.")
        return 0

    if not args.no_backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.db.with_name(f"{args.db.name}.pre-v1.4.0.{stamp}")
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
