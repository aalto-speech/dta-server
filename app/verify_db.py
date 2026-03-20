import sqlite3

from .config import DATABASE


def main() -> None:
    """Verify the database schema and print table information."""
    # pylint: disable=too-many-locals

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Get all table names (excluding internal SQLite tables)
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
    )
    tables = cursor.fetchall()

    print("Database Tables:")
    for table in tables:
        table_name = table[0]
        print(f"\nTable: {table_name}")

        # Get column info
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()

        print("  Columns:")
        for col in columns:
            primary_key = " PRIMARY KEY" if col[5] else ""
            print(f"    {col[1]}: {col[2]}{primary_key}")

        # Get index info
        cursor.execute(f"PRAGMA index_list({table_name})")
        indexes = cursor.fetchall()

        print("  Indexes:")
        if not indexes:
            print("    (none)")
        else:
            for idx in indexes:
                idx_name = idx[1]
                is_unique = " UNIQUE" if idx[2] else ""
                idx_origin = idx[3] if len(idx) > 3 else "?"

                cursor.execute(f"PRAGMA index_info({idx_name})")
                idx_cols = [row[2] for row in cursor.fetchall()]
                idx_cols_str = ", ".join(
                    idx_cols) if idx_cols else "(expression/unknown)"

                print(
                    f"    {idx_name}{is_unique} [origin={idx_origin}]: {idx_cols_str}"
                )

        # Get row count
        cursor.execute(f"SELECT COUNT(1) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"  Number of objects: {count}")

    conn.close()


if __name__ == "__main__":
    main()
