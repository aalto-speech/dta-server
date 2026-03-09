import logging
import os
import sqlite3
import tempfile
from random import uniform


import whisper
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .models import SpeechAssessment, SpeechAssessmentScores
from .validate import (
    _validate_content_type,
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
async def setup_database():
    """Initialize database on app startup."""

    conn = sqlite3.connect('speech_assessments.db')
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()
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
        range REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()


@app.get("/ping")
async def ping() -> JSONResponse:
    """Ping-pong endpoint for checking if the server is running."""
    return JSONResponse(content={"message": "Pong!"}, status_code=200)


@app.post("/speech/assess")
async def assess_speech(file: UploadFile = File(...)) -> JSONResponse:
    """Assess speech using the AI model.

    Security checks performed before processing:
    1. Content-Type must indicate a WAV audio file.
    2. Filename must end with .wav (no path-traversal characters).
    3. File size must not exceed MAX_FILE_SIZE (streamed in chunks).
    4. RIFF/WAVE magic bytes must be present.
    5. stdlib `wave` module must parse the file without errors.
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
