import sqlite3

# Connect to SQLite database (creates it if it doesn't exist)
conn = sqlite3.connect('speech_assessments.db')

# Enable WAL mode for better concurrency
conn.execute('PRAGMA journal_mode=WAL;')

# Create a cursor object
cursor = conn.cursor()

# Create table
cursor.execute('''
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    transcript TEXT NOT NULL,
    accuracy REAL,
    fluency REAL,
    proficiency REAL,
    pronunciation REAL,
    range_score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Commit the changes
conn.commit()

# Close the connection
conn.close()

print("Database and table created successfully with WAL mode enabled!")
