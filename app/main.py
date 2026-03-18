import logging
import os
import sqlite3
import tempfile
from random import uniform

import whisper
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from .db import create_user
from .models import OnboardingRequest, SpeechAssessment, SpeechAssessmentScores
from .validate import (
    _validate_audio_duration,
    _validate_content_type,
    _validate_feedback,
    _validate_file_name,
    _validate_file_size,
    _validate_wav_headers,
    _validate_wav_structure,
)

app = FastAPI()
logger = logging.getLogger(__name__)

# Load whisper model once at startup
whisper_model = whisper.load_model("small")


@app.on_event("startup")
# on_event is deprecated, use lifespan event handlers instead.
#         Read more about it in the
#         [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
async def setup_database() -> None:
    """Initialize database on app startup."""

    conn = sqlite3.connect('speech_assessments.db')
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        guid TEXT PRIMARY KEY,                         -- pseudonymous user ID (GUID)
        consent_accepted INTEGER NOT NULL CHECK (consent_accepted IN (0, 1)),
        consent_timestamp TEXT NOT NULL,              -- ISO 8601 timestamp
        app_version TEXT,                             -- app version shown during consent
        gender TEXT NOT NULL CHECK (
            gender IN ('woman', 'man', 'other', 'prefer_not_to_answer')
        ),
        age_group TEXT NOT NULL CHECK (
            age_group IN ('18-28', '29-39', '40-50', '51-61', '62_or_older')
        ),

        -- Store multi-select fields as JSON text arrays, e.g. '["Vietnamese","English"]'
        mother_tongues TEXT NOT NULL,
        other_languages TEXT NOT NULL,
        moved_to_finland TEXT NOT NULL,               -- e.g. '2025', '2024', ... '2015', 'before_2015'
        finnish_learning_duration TEXT NOT NULL,      -- use the exact questionnaire options
        finnish_self_assessment TEXT NOT NULL CHECK (
            finnish_self_assessment IN ('A1', 'A2', 'B1', 'B2', 'C1_or_higher')
        ),

        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );


    -- 2) ASSESSMENTS TABLE
    -- Stores one row per ASA attempt.
    -- Audio file itself is stored in persistent storage outside the container.
    -- audio_id and audio_path are metadata pointing to that stored file.
    -- =========================================================
    CREATE TABLE IF NOT EXISTS assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,         -- assessment_id
        guid TEXT NOT NULL,
        task_id TEXT NOT NULL,                        -- speaking task ID / prompt ID

        audio_id TEXT NOT NULL UNIQUE,                -- unique ID for audio file
        audio_path TEXT NOT NULL,                     -- persistent storage path / object key

        transcript TEXT,                              -- optional ASR transcript

        -- ASA model outputs
        accuracy REAL,
        fluency REAL,
        proficiency REAL,
        pronunciation REAL,
        range REAL,

        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (guid) REFERENCES users(guid) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,         -- feedback_id
        guid TEXT NOT NULL,
        assessment_id INTEGER,                        -- nullable if feedback is not about a specific assessment

        target_type TEXT NOT NULL CHECK (
            target_type IN ('assessment', 'rating_ui', 'comparison_ui', 'general_experience') -- insert more if needed
        ),

        reaction_value INTEGER NOT NULL CHECK (
            reaction_value BETWEEN 1 AND 5
        ),                                            -- 1 = very sad ... 5 = very happy

        comment TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (guid) REFERENCES users(guid) ON DELETE CASCADE,
        FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE
    );
    ''')
    conn.commit()
    conn.close()


@app.get("/ping")
async def ping() -> JSONResponse:
    """Ping-pong endpoint for checking if the server is running."""
    return JSONResponse(content={"message": "Pong!"}, status_code=200)


@app.post("/feedback")
async def feedback(
    guid: str = Form(...),
    assessment_id: int | None = Form(None),
    target_type: str = Form(...),
    reaction_value: int = Form(...),
    comment: str | None = Form(None)
) -> JSONResponse:
    """Payload:
    guid
    assessment_id
    target_type
    reaction_value
    comment"""

    _validate_feedback(guid, assessment_id, target_type,
                       reaction_value, comment)

    conn = sqlite3.connect('speech_assessments.db')
    conn.execute(
        '''INSERT INTO feedback (guid, assessment_id, target_type, reaction_value, comment)
           VALUES (?, ?, ?, ?, ?)''',
        (guid, assessment_id, target_type, reaction_value, comment)
    )
    conn.commit()
    conn.close()

    return JSONResponse(content={"status": "feedback recorded"}, status_code=201)


@app.post("/speech/assess")
async def assess_speech(file: UploadFile = File(...)) -> JSONResponse:
    """Assess speech using the AI model.

    Security checks performed before processing:
    1. Content-Type must indicate a WAV audio file.
    2. Filename must end with .wav (no path-traversal characters).
    3. File size must not exceed MAX_FILE_SIZE (streamed in chunks).
    4. RIFF/WAVE magic bytes must be present.
    5. stdlib `wave` module must parse the file without errors.
    6. Audio length must be within constraints, by default less than 90 seconds.
    """

    # --- 1. Validate Content-Type to reject obviously wrong uploads ---
    _validate_content_type(file)

    # --- 2. Validate and sanitise the filename ---
    filename = file.filename or ""
    _validate_file_name(filename)

    # --- 3. Stream the upload in chunks and enforce the size limit ---
    content = await _validate_file_size(file)

    # --- 4. Validate RIFF/WAVE magic bytes ---
    _validate_wav_headers(content)

    # --- 5. Write to a secure temp file and validate WAV structure ---
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".wav", prefix="dta_"
        ) as f:
            f.write(content)
            temp_path = f.name

        # Set restrictive permissions so only the owner can read the file
        os.chmod(temp_path, 0o600)

        # Parse with stdlib wave to confirm structural validity
        _validate_wav_structure(temp_path)

        # --- 6. Validate audio duration ---
        _validate_audio_duration(temp_path)

        # --- Processing ---
        # Transcribe audio using whisper (force Finnish language for better accuracy)
        result = whisper_model.transcribe(temp_path, language="fi")
        text = result["text"]
        transcript = " ".join(text) if isinstance(text, list) else str(text)

        # Placeholder assessment scores (replace with actual ML logic)
        accuracy = round(uniform(0, 5), 1)
        fluency = round(uniform(0, 5), 1)
        proficiency = round(uniform(0, 5), 1)
        pronunciation = round(uniform(0, 5), 1)
        range_score = round(uniform(0, 5), 1)

        json = jsonable_encoder(
            SpeechAssessment(
                scores=SpeechAssessmentScores(
                    accuracy=accuracy,
                    fluency=fluency,
                    proficiency=proficiency,
                    pronunciation=pronunciation,
                    range=range_score,
                ),
                transcript=transcript,
            )
        )
        return JSONResponse(content=json, status_code=200)
    except HTTPException:
        # Re-raise validation errors as-is
        raise
    except Exception as err:  # pylint: disable=broad-exception-caught
        # Log the real error for debugging but never leak internals
        logger.exception(err)
        return JSONResponse(
            content={"detail": "Internal server error"}, status_code=500
        )
    finally:
        # Always clean up the temporary file
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@app.post("/onboarding")
async def onboarding(payload: OnboardingRequest) -> Response:
    """Onboarding endpoint for new users."""

    # Validation should be handled by Pydantic model parsing
    create_user(payload)

    return Response(status_code=200)
