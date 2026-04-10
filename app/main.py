from contextlib import asynccontextmanager
from time import monotonic

from fastapi import Depends, FastAPI, Form
from fastapi.responses import JSONResponse, Response

from .config import SETTINGS
from .db import initialize_database
from .error_handlers import register_error_handlers
from .models.analytics import ComparisonRequest
from .models.feedback import FeedbackRequest
from .models.onboarding import OnboardingRequest
from .models.speech_assessment import SpeechAssessmentRequest
from .models.user_requests import DeleteUserRequest, UserDataRequest
from .services.admin_service import delete_user
from .services.analytics_service import get_comparison
from .services.feedback_service import record_feedback
from .services.onboarding_service import create_onboarding_user
from .services.speech_assessment_service import assess_speech_request
from .services.user_request_service import handle_user_request
from .utils.logger import get_logger

logger = get_logger(__name__)
APP_START_MONOTONIC = monotonic()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Run startup/shutdown logic for the FastAPI app."""

    db_initialized = initialize_database()
    db_status_message = "initialized" if db_initialized else "already exists"

    logger.info("Database %s at %s", db_status_message, SETTINGS.database)
    logger.info("Application started in %s environment", SETTINGS.env)

    yield


# Set root_path to /api/v1 to ensure correct routing when behind a reverse proxy with a base path.
app = FastAPI(lifespan=lifespan, root_path="/api/v1",)
register_error_handlers(app, logger)


@app.get("/ping")
async def ping() -> JSONResponse:
    """Health-check endpoint.

    Returns:
        JSONResponse: 200 with a static pong message.
    """

    return JSONResponse(content={"message": "Pong!"}, status_code=200)


@app.get("/status")
async def status() -> JSONResponse:
    """Application status endpoint with uptime.

    Returns:
        JSONResponse: 200 with environment and process uptime in seconds.
    """

    uptime_seconds = round(monotonic() - APP_START_MONOTONIC, 3)
    return JSONResponse(
        content={
            "status": "ok",
            "env": SETTINGS.env,
            "uptime_seconds": uptime_seconds,
        },
        status_code=200,
    )


@app.post("/analytics/comparison")
async def analytics_comparison(data: ComparisonRequest = Form()) -> JSONResponse:
    """Return cohort comparison stats for the requesting user.

    Args:
        data: Comparison request payload including user GUID and window options.

    Returns:
        JSONResponse: 200 with percentile/rank data or comparison unavailable status.
    """

    return get_comparison(data)


@app.post("/request/user")
async def request_user(data: UserDataRequest = Form()) -> JSONResponse:
    """Submit a user data request (delete or export).

    Delete requests are stored for admin approval.
    Export requests currently return not implemented.

    Args:
        data: User request payload with GUID and request type.

    Returns:
        JSONResponse: 202 for delete requests, 501 for export requests.
    """

    return handle_user_request(data)


@app.post("/feedback")
async def feedback(data: FeedbackRequest = Form()) -> JSONResponse:
    """Submit assessment or experience feedback.

    Args:
        data: Feedback payload including classification, score, and optional comment.

    Returns:
        JSONResponse: 201 when feedback is accepted.
    """

    return record_feedback(data)


@app.post("/speech/assess")
async def assess_speech(
    data: SpeechAssessmentRequest = Depends(SpeechAssessmentRequest.as_form)
) -> JSONResponse:
    """Assess uploaded speech audio and return scores with transcript.

    Args:
        data: Speech assessment form payload with user GUID and WAV file.

    Returns:
        JSONResponse: 200 with generated scores and transcription result.
    """

    return await assess_speech_request(data)


@app.post("/onboarding")
async def onboarding(data: OnboardingRequest = Form()) -> Response:
    """Create a new user from onboarding form data.

    Args:
        data: Onboarding payload containing profile and language background fields.

    Returns:
        Response: 201 when the user is created.
    """

    return create_onboarding_user(data)


@app.delete("/users")
async def delete_users(
    data: DeleteUserRequest = Depends(
        DeleteUserRequest.as_form)
) -> Response:
    """Delete all persisted data for a user.

    This is an admin-only endpoint protected by API key validation.

    Args:
        data: Delete payload containing target GUID and admin API key.

    Returns:
        Response: 204 when deletion succeeds.
    """

    return delete_user(data)
