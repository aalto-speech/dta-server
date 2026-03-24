import logging
import os
import tempfile
from contextlib import asynccontextmanager
from random import uniform

import whisper
from fastapi import Depends, FastAPI, Form
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
from .models.speech_assessment import (
    SpeechAssessmentRequest,
    SpeechAssessmentResponse,
    SpeechAssessmentScores,
)
from .models.user_requests import DeleteUserRequest, UserDataRequest
from .validators import audio, auth

logger = logging.getLogger(__name__)

# Load whisper model once at startup
whisper_model = whisper.load_model("small")


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


@app.post("/request/user")
async def request_user(data: UserDataRequest = Form()) -> JSONResponse:
    """User request for deleting or exporting their data.

    The request is stored in the database and awaits admin approval.

    Args:
        data: UserDataRequest containing the user's GUID

    Returns:
        JSONResponse with status message
    """

    # * Pydantic and global error handlers handle validation and failures.
    create_user_request(data)

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
async def feedback(data: FeedbackRequest = Form()) -> JSONResponse:
    """Submit feedback related to assessments or app experience.

    Args:
        data: FeedbackRequest containing feedback details
    """

    # * Pydantic and global error handlers handle validation and failures.
    create_feedback(data)

    return JSONResponse(content={"status": "feedback recorded"}, status_code=201)


@app.post("/speech/assess")
async def assess_speech(
    data: SpeechAssessmentRequest = Depends(SpeechAssessmentRequest.as_form)
) -> JSONResponse:
    """Assess the uploaded speech audio file and return scores and transcript.

    Args:
        data: SpeechAssessmentRequest containing the uploaded file and user GUID

    Security checks:
        1. Validate the uploaded file is a WAV audio file with correct content type and extension.
        2. Enforce file size limit by streaming the upload in chunks.
        3. Validate RIFF/WAVE magic bytes are present in the file header.
        4. Write the file to a secure temporary location and validate its structure using the stdlib `wave` module.
        5. Validate the audio duration is within acceptable limits (e.g., less than 90 seconds).

    Returns:
        JSONResponse SpeechAssessmentResponse containing the assessment scores and transcript.
    """

    # * Pydantic validation ensures the content type and file extension are correct.
    # * Validation checks which require access to the file content are not performed by Pydantic.

    auth.validate_user_access(data.guid)

    file = data.file

    # Stream the upload in chunks and enforce the size limit
    content = await audio.validate_file_size(file)

    # Validate RIFF/WAVE magic bytes
    audio.validate_wav_headers(content)

    # Write to a secure temp file and validate WAV structure
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
        audio.validate_wav_structure(temp_path)

        # Validate audio duration
        audio.validate_audio_duration(temp_path)

        # TODO: Move to an asynchronous method for better performance and scalability.
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
async def onboarding(data: OnboardingRequest = Form()) -> Response:
    """Onboarding endpoint for new users.

    Args:
        data: OnboardingRequest containing the user's onboarding information

    Returns:
        Response with status code 201 on success
    """

    # * Pydantic and global error handlers handle validation and failures.
    create_user(data)

    return Response(status_code=201)


@app.delete("/users")
async def delete_users(
    data: DeleteUserRequest = Depends(
        DeleteUserRequest.as_form)
) -> JSONResponse:
    """Delete all data for a user with the given GUID.

    This endpoint is admin-only and requires a valid API key.
    Deletes all associated data from users, assessments, and feedback tables.

    Args:
        data: DeleteUserRequest containing the user's GUID and admin API key

    Returns:
        JSONResponse with deletion summary
    """

    # Validate admin access
    auth.validate_admin_access(data.api_key)

    # * Pydantic and global error handlers handle validation and failures.
    delete_user_data(data)

    logger.info("Admin deleted all data for user: %s", data.guid)

    return Response(status_code=204)
