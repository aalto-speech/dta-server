"""In-place migration from the v1.2.0 schema to v1.3.0. No database recreation.

v1.2.0 needed the database rebuilt because SQLite cannot alter a CHECK constraint. This
one does not: everything here is ADD COLUMN, CREATE INDEX, or a table rebuild scoped to
`feedback`, so the assessments and audio already collected survive.

WHAT IT DOES

  users     + current_cefr_level      the level a learner is working at (PATCH /users/level).
                                      NULL for every existing row, which reads as "never
                                      moved" -- the onboarding self-assessment is untouched.
  feedback  + updated_at              when the learner last changed the answer.
            + UNIQUE (guid, assessment_id, type)
                                      one row per learner per question per recording.
                                      Existing duplicates are collapsed to the NEWEST row
                                      first, because the index cannot be built over them.
  user_cefr_history                   backfilled with each existing user's starting level,
                                      so the trail does not begin mid-story.

Run with the stack STOPPED. Idempotent: every step checks whether it has already run, so a
second invocation is a no-op and a failed run can simply be repeated.

    python3 scripts/dta_production/migrate_v1_3_0.py --db /path/to/dta.db          # dry run
    python3 scripts/dta_production/migrate_v1_3_0.py --db /path/to/dta.db --apply
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _indexes(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA index_list({table})")}


def _duplicate_feedback(db: sqlite3.Connection) -> list[tuple]:
    return db.execute(
        """
        SELECT guid, assessment_id, type, COUNT(*) AS n
        FROM feedback
        WHERE assessment_id IS NOT NULL
        GROUP BY guid, assessment_id, type
        HAVING n > 1
        ORDER BY n DESC
        """
    ).fetchall()


def plan(db: sqlite3.Connection) -> list[str]:
    """What this run would change, in the order it would happen."""

    steps = []

    if "current_cefr_level" not in _columns(db, "users"):
        steps.append("users: ADD COLUMN current_cefr_level")

    missing_history = db.execute(
        "SELECT COUNT(*) FROM users u WHERE NOT EXISTS ("
        "  SELECT 1 FROM user_cefr_history h WHERE h.guid = u.guid)"
    ).fetchone()[0]
    if missing_history:
        steps.append(f"user_cefr_history: backfill {missing_history} starting level(s)")

    duplicates = _duplicate_feedback(db)
    if duplicates:
        losing = sum(n - 1 for *_, n in duplicates)
        steps.append(
            f"feedback: collapse {len(duplicates)} duplicated answer(s), "
            f"deleting {losing} superseded row(s)")

    if "updated_at" not in _columns(db, "feedback"):
        steps.append("feedback: rebuild table to add updated_at "
                     "(rows carried across, updated_at seeded from created_at)")

    if "idx_feedback_one_answer_per_question" not in _indexes(db, "feedback"):
        steps.append("feedback: CREATE UNIQUE INDEX idx_feedback_one_answer_per_question")

    return steps


def _rebuild_feedback(db: sqlite3.Connection) -> None:
    """Replace `feedback` with the v1.3.0 shape, carrying every row across.

    A rebuild rather than ADD COLUMN, because `updated_at` is `NOT NULL DEFAULT
    CURRENT_TIMESTAMP` in schema.sql and SQLite refuses a non-constant default on ALTER
    TABLE ADD COLUMN once the table has rows. Adding it nullable and without a default
    would leave a migrated database subtly different from a fresh one -- and worse than
    cosmetically: `create_feedback` does not name `updated_at` in its INSERT, so every new
    row would silently store NULL where a fresh database stores the timestamp.

    Existing rows inherit `created_at` as their `updated_at`: nobody edited them at
    migration time, and stamping them with now() would be a lie the analysis cannot see
    through. tests/test_migration_v1_3_0.py asserts the result matches schema.sql.

    The DDL below is a copy of the `feedback` block in app/schema.sql. That duplication is
    what the test exists to police.
    """

    # Foreign keys must be off for the drop-and-rename; SQLite documents this as step 1 of
    # the table-rebuild procedure. The caller's connection has them on, and the
    # foreign_key_check in verify() is what confirms nothing was orphaned.
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute(
            """
            CREATE TABLE feedback_v1_3_0 (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              guid TEXT NOT NULL,
              assessment_id INTEGER,
              type TEXT NOT NULL CHECK (
                type IN (
                  'self_assessment',
                  'result_accuracy',
                  'result_understanding',
                  'comparison_ui',
                  'overall_experience'
                )
              ),
              reaction_value INTEGER NOT NULL CHECK (reaction_value BETWEEN 1 AND 5),
              comment TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY (guid) REFERENCES users (guid) ON DELETE CASCADE,
              FOREIGN KEY (assessment_id) REFERENCES assessments (id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            """
            INSERT INTO feedback_v1_3_0 (
              id, guid, assessment_id, type, reaction_value, comment,
              created_at, updated_at
            )
            SELECT id, guid, assessment_id, type, reaction_value, comment,
                   created_at, created_at
            FROM feedback
            """
        )
        db.execute("DROP TABLE feedback")
        db.execute("ALTER TABLE feedback_v1_3_0 RENAME TO feedback")
        # The rename does not carry the old indexes; schema.sql's are recreated here and
        # the unique one is added by the caller.
        db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_guid_created_at "
                   "ON feedback (guid, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_assessment_id "
                   "ON feedback (assessment_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_type_guid_created_at "
                   "ON feedback (type, guid, created_at)")
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def migrate(db: sqlite3.Connection) -> None:
    """Apply every outstanding step. Safe to run twice."""

    if "current_cefr_level" not in _columns(db, "users"):
        db.execute(
            "ALTER TABLE users ADD COLUMN current_cefr_level TEXT "
            "CHECK (current_cefr_level IS NULL OR current_cefr_level IN "
            "('A1', 'A2', 'B1', 'B2', 'C1_plus'))")

    # Seed the trail with where each existing learner started. Their created_at is used
    # rather than now(), so the history does not claim they chose their level today.
    db.execute(
        """
        INSERT INTO user_cefr_history (guid, cefr_level, source, created_at)
        SELECT u.guid, u.cefr_level, 'self_report', u.created_at
        FROM users u
        WHERE NOT EXISTS (SELECT 1 FROM user_cefr_history h WHERE h.guid = u.guid)
        """
    )

    # The newest row is the learner's final answer; everything before it is a keystroke.
    # This has to happen before the unique index, which cannot be built over duplicates.
    db.execute(
        """
        DELETE FROM feedback
        WHERE assessment_id IS NOT NULL
          AND id NOT IN (
            SELECT MAX(id) FROM feedback
            WHERE assessment_id IS NOT NULL
            GROUP BY guid, assessment_id, type
          )
        """
    )

    if "updated_at" not in _columns(db, "feedback"):
        _rebuild_feedback(db)

    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_one_answer_per_question "
        "ON feedback (guid, assessment_id, type)")


def verify(db: sqlite3.Connection) -> list[str]:
    """Post-conditions. Returns a list of problems; empty means the migration held."""

    problems = []

    if "current_cefr_level" not in _columns(db, "users"):
        problems.append("users.current_cefr_level is missing")
    if "updated_at" not in _columns(db, "feedback"):
        problems.append("feedback.updated_at is missing")
    if "idx_feedback_one_answer_per_question" not in _indexes(db, "feedback"):
        problems.append("the feedback unique index is missing")
    if _duplicate_feedback(db):
        problems.append("duplicate feedback answers survived")

    orphans = db.execute(
        "SELECT COUNT(*) FROM users u WHERE NOT EXISTS ("
        "  SELECT 1 FROM user_cefr_history h WHERE h.guid = u.guid)"
    ).fetchone()[0]
    if orphans:
        problems.append(f"{orphans} user(s) have no history row")

    nulls = db.execute(
        "SELECT COUNT(*) FROM feedback WHERE updated_at IS NULL").fetchone()[0]
    if nulls:
        problems.append(f"{nulls} feedback row(s) have a NULL updated_at")

    # The self-assessment must survive untouched -- that is the whole point of the new
    # column. Every user should still have one, and it should still be a valid level.
    bad = db.execute(
        "SELECT COUNT(*) FROM users WHERE cefr_level IS NULL "
        "OR cefr_level NOT IN ('A1','A2','B1','B2','C1_plus')").fetchone()[0]
    if bad:
        problems.append(f"{bad} user(s) have a damaged cefr_level")

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

        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "assessments", "feedback", "user_cefr_history")
        }
        print("before: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

        if not steps:
            print("nothing to do: the database is already on the v1.3.0 schema")
            return 0

        print("\nplanned:")
        for step in steps:
            print(f"  - {step}")

        if not args.apply:
            print("\ndry run. re-run with --apply to write these changes.")
            return 0

    if not args.no_backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.db.with_name(f"{args.db.name}.pre-v1.3.0.{stamp}")
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
        problems = verify(db)
        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "assessments", "feedback", "user_cefr_history")
        }

    print("after:  " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    if problems:
        print("\nFAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("\nmigration complete and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
