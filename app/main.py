import logging
import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from random import uniform
from typing import Annotated

import whisper
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from .config import ADMIN_API_KEY, DATABASE
from .db import create_user, create_user_request
from .models.feedback import FeedbackRequest
from .models.onboarding import OnboardingRequest
from .models.speech_assessment import SpeechAssessment, SpeechAssessmentScores
from .models.user_data_request import UserDataDeleteRequest
from .validate import (
    _validate_audio_duration,
    _validate_content_type,
    _validate_file_name,
    _validate_file_size,
    _validate_wav_headers,
    _validate_wav_structure,
)

logger = logging.getLogger(__name__)

# Load whisper model once at startup
whisper_model = whisper.load_model("small")


def _validate_admin_access(api_key: str = Header(...)) -> None:
    """Validate admin API key for protected endpoints.

    Args:
        api_key: The API key from the X-API-Key header

    Raises:
        HTTPException: If the API key is invalid or missing
    """

    if not ADMIN_API_KEY:
        raise HTTPException(
            status_code=500, detail="Admin API key not configured")
    if api_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=403, detail="Invalid or missing API key")


def initialize_database() -> None:
    """Initialize database on app startup."""

    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA journal_mode=WAL;")
    schema_sql = Path(__file__).with_name(
        "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Run startup/shutdown logic for the FastAPI app."""

    initialize_database()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
async def ping() -> JSONResponse:
    """Ping-pong endpoint for checking if the server is running."""
    return JSONResponse(content={"message": "Pong!"}, status_code=200)


@app.post("/request/delete/userdata")
async def request_delete_userdata(request: UserDataDeleteRequest) -> JSONResponse:
    """User requests deletion of their personal data.

    The request is stored in the database and awaits admin approval.

    Args:
        request: UserDataDeleteRequest containing the user's GUID

    Returns:
        JSONResponse with status message
    """
    create_user_request(request.guid, "delete_data")
    return JSONResponse(
        content={
            "status": "request_received",
            "message": (
                "Your data deletion request has been received "
                + "and is awaiting admin approval"
            ),
        },
        status_code=202,
    )


@app.post("/feedback")
async def feedback(
    payload: FeedbackRequest = Form(...),
) -> JSONResponse:
    """Submit feedback related to assessments or app experience.

    Args:
        payload: FeedbackRequest containing feedback details
    """

    # Pydantic will handle validation of the request body.

    conn = sqlite3.connect(DATABASE)
    conn.execute(
        """INSERT INTO feedback (
            guid,
            assessment_id,
            feedback_type,
            reaction_value,
            comment
        ) VALUES (?, ?, ?, ?, ?)""",
        (payload.guid, payload.assessment_id, payload.feedback_type,
         payload.reaction_value, payload.comment),
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
async def onboarding(
    payload: Annotated[OnboardingRequest, Depends(OnboardingRequest.as_form)]
) -> Response:
    """Onboarding endpoint for new users."""

    # Validation should be handled by Pydantic model parsing
    create_user(payload)

    return Response(status_code=200)


# ? Should admin endpoints be structured under an /admin path? e.g. POST /admin/delete/userdata
@app.delete("/delete/userdata")
async def delete_userdata(
    guid: str = Form(...),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> JSONResponse:
    """Delete all data for a user with the given GUID.

    This endpoint is admin-only and requires a valid API key.
    Deletes all associated data from users, assessments, and feedback tables.

    Args:
        guid: The user GUID to delete all data for
        x_api_key: Admin API key passed in X-API-Key header

    Returns:
        JSONResponse with deletion summary

    Raises:
        HTTPException: If API key is invalid or user not found
    """
    # Validate admin access
    _validate_admin_access(x_api_key)

    if not guid or not isinstance(guid, str) or not guid.strip():
        raise HTTPException(status_code=400, detail="Invalid or missing GUID")

    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()

        # Check if user exists
        cursor.execute("SELECT guid FROM users WHERE guid = ?", (guid,))
        user = cursor.fetchone()

        if not user:
            raise HTTPException(
                status_code=404, detail=f"User with GUID {guid} not found"
            )

        # Delete user (cascade will handle assessments and feedback)
        cursor.execute("DELETE FROM users WHERE guid = ?", (guid,))

        conn.commit()
        conn.close()

        logger.info("Admin deleted all data for user: %s", guid)

        return JSONResponse(
            content={
                "status": "success",
                "message": f"User data deleted for GUID: {guid}",
            },
            status_code=200,
        )
    except HTTPException:
        raise
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.exception("Error deleting user data for %s: %s", guid, err)
        return JSONResponse(
            content={"detail": "Internal server error"}, status_code=500
        )
