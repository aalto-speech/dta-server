CREATE TABLE
  IF NOT EXISTS users (
    guid TEXT PRIMARY KEY, -- pseudonymous user ID (GUID)
    consent_accepted INTEGER NOT NULL CHECK (consent_accepted IN (0, 1)),
    consent_timestamp TEXT NOT NULL, -- ISO 8601 timestamp
    app_version TEXT, -- app version shown during consent
    gender TEXT NOT NULL CHECK (
      gender IN ('woman', 'man', 'other', 'prefer_not_to_answer')
    ),
    age_group TEXT NOT NULL CHECK (
      age_group IN (
        'age_18_28',
        'age_29_39',
        'age_40_50',
        'age_51_61',
        'age_62_plus'
      )
    ),
    -- Store multi-select fields as JSON text arrays, e.g. '["Vietnamese","English"]'
    native_languages TEXT NOT NULL CHECK (
      json_valid (native_languages)
      AND json_type (native_languages) = 'array'
      AND json_array_length (native_languages) > 0
    ),
    other_languages TEXT NOT NULL CHECK (
      json_valid (other_languages)
      AND json_type (other_languages) = 'array'
    ),
    moved_to_finland TEXT NOT NULL CHECK (
      moved_to_finland = 'before_2015'
      OR (
        length (moved_to_finland) = 4
        AND moved_to_finland GLOB '[0-9][0-9][0-9][0-9]'
        AND CAST(moved_to_finland AS INTEGER) >= 2015
        AND CAST(moved_to_finland AS INTEGER) <= 2100 -- * make sure it's a reasonable year
      )
    ), -- e.g. '2025', '2024', ... '2015', 'before_2015'
    finnish_learning_duration TEXT NOT NULL CHECK (
      finnish_learning_duration IN (
        'months_0_3',
        'months_3_6',
        'months_6_9',
        'months_9_12',
        'years_1_1.5',
        'years_1.5_2',
        'years_2_2.5',
        'years_2.5_3',
        'years_3_5',
        'years_5_7',
        'years_7_10',
        'years_10_plus'
      )
    ),
    cefr_level TEXT NOT NULL CHECK (cefr_level IN ('A1', 'A2', 'B1', 'B2', 'C1_plus')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
  );

CREATE TABLE
  IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- assessment_id
    guid TEXT NOT NULL,
    task_id INTEGER NOT NULL, -- speaking task ID
    audio_id TEXT NOT NULL UNIQUE, -- unique ID for audio file
    audio_path TEXT NOT NULL, -- persistent storage path / object key
    transcript TEXT, -- optional ASR transcript
    -- ASA model outputs
    accuracy REAL CHECK (
      accuracy IS NULL
      OR (accuracy BETWEEN 0 AND 5)
    ),
    fluency REAL CHECK (
      fluency IS NULL
      OR (fluency BETWEEN 0 AND 5)
    ),
    proficiency REAL CHECK (
      proficiency IS NULL
      OR (proficiency BETWEEN 0 AND 5)
    ),
    pronunciation REAL CHECK (
      pronunciation IS NULL
      OR (pronunciation BETWEEN 0 AND 5)
    ),
    range_score REAL CHECK (
      range_score IS NULL
      OR (range_score BETWEEN 0 AND 5)
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guid) REFERENCES users (guid) ON DELETE CASCADE
  );

CREATE TABLE
  IF NOT EXISTS feedback (
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
    FOREIGN KEY (guid) REFERENCES users (guid) ON DELETE CASCADE,
    FOREIGN KEY (assessment_id) REFERENCES assessments (id) ON DELETE CASCADE
  );

CREATE TABLE
  IF NOT EXISTS user_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, -- request_id
    guid TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('delete', 'export')), -- type of user request
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
      status IN ('pending', 'approved', 'denied', 'completed')
    ), -- request processing status
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT, -- timestamp when admin processed the request
    admin_notes TEXT, -- optional notes from admin
    FOREIGN KEY (guid) REFERENCES users (guid) ON DELETE CASCADE
  );

CREATE TABLE
  IF NOT EXISTS user_cefr_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT NOT NULL,
    cefr_level TEXT NOT NULL CHECK (cefr_level IN ('A1', 'A2', 'B1', 'B2', 'C1_plus')),
    source TEXT NOT NULL CHECK (source IN ('self_report', 'model')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guid) REFERENCES users (guid) ON DELETE CASCADE
  );

-- Indexes to optimize queries by guid and created_at for assessments and feedback tables
CREATE INDEX IF NOT EXISTS idx_assessments_guid_created_at ON assessments (guid, created_at);

CREATE INDEX IF NOT EXISTS idx_assessments_task_id ON assessments (task_id);

CREATE INDEX IF NOT EXISTS idx_feedback_guid_created_at ON feedback (guid, created_at);

CREATE INDEX IF NOT EXISTS idx_feedback_assessment_id ON feedback (assessment_id);

CREATE INDEX IF NOT EXISTS idx_feedback_type_guid_created_at ON feedback (type, guid, created_at);

CREATE INDEX IF NOT EXISTS idx_user_cefr_history_guid_created_at_id ON user_cefr_history (guid, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_assessments_guid_proficiency_not_null ON assessments (guid, proficiency)
WHERE
  proficiency IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_users_cefr_level ON users (cefr_level);
