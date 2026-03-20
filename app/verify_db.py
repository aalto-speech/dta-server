import sqlite3
import os

from dotenv import load_dotenv

load_dotenv()

DATABASE = os.getenv("DATABASE", "dta.db")

conn = sqlite3.connect(DATABASE)
cursor = conn.cursor()

# Get all table names (excluding internal SQLite tables)
cursor.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
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
        pk = " PRIMARY KEY" if col[5] else ""
        print(f"    {col[1]}: {col[2]}{pk}")

    # Get row count
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"  Number of objects: {count}")

conn.close()
