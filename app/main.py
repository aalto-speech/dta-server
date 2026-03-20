import logging
import os
import tempfile
from contextlib import asynccontextmanager
from random import uniform
from typing import Annotated

import whisper
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from .config import SETTINGS
from .db import (
    create_feedback,
    create_user,
    create_user_request,
    delete_user_data,
    initialize_database,
)
from .error_handlers import register_error_handlers
from .models.feedback import FeedbackRequest
from .models.onboarding import OnboardingRequest
from .models.speech_assessment import SpeechAssessmentResponse, SpeechAssessmentScores
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
        HTTPException: If the API key is invalid
    """

    # pylint: disable=fixme
    # TODO: Refactor into a helper function.

    if api_key != SETTINGS.admin_api_key:
        raise HTTPException(
            status_code=403, detail="Invalid API key")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Run startup/shutdown logic for the FastAPI app."""

    status = initialize_database()
    db_status_message = "initialized" if status else "already exists"

    logger.info("Database %s at %s", db_status_message, SETTINGS.database)
    logger.info("Application started in %s environment", SETTINGS.env)

    yield


# Set root_path to /api/v1 to ensure correct routing when behind a reverse proxy with a base path.
app = FastAPI(lifespan=lifespan, root_path="/api/v1",)
register_error_handlers(app, logger)


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
async def feedback(data: Annotated[FeedbackRequest, Form()]) -> JSONResponse:
    """Submit feedback related to assessments or app experience.

    Args:
        data: FeedbackRequest containing feedback details
    """

    # Pydantic and global error handlers handle validation and failures.
    create_feedback(data)

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

        data = SpeechAssessmentResponse(
            scores=SpeechAssessmentScores(
                accuracy=accuracy,
                fluency=fluency,
                proficiency=proficiency,
                pronunciation=pronunciation,
                range=range_score,
            ),
            transcript=transcript
        )

        return JSONResponse(content=jsonable_encoder(data), status_code=200)
    finally:
        # Always clean up the temporary file
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@app.post("/onboarding")
async def onboarding(data: Annotated[OnboardingRequest, Form()]) -> Response:
    """Onboarding endpoint for new users."""

    create_user(data)

    return Response(status_code=201)


# ? Should admin endpoints be structured under an /admin path? e.g. POST /admin/delete/userdata
@app.delete("/delete/userdata")
async def delete_userdata(guid: str = Form(...),
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

    delete_user_data(guid)

    logger.info("Admin deleted all data for user: %s", guid)

    return JSONResponse(
        content={
            "status": "success",
            "message": f"User data deleted for GUID: {guid}",
        },
        status_code=200,
    )
