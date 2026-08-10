from contextlib import asynccontextmanager
from time import monotonic

from fastapi import Depends, FastAPI, Form
from fastapi.responses import JSONResponse, Response

from app.config import SETTINGS
from app.db import initialize_database
from app.error_handlers import register_error_handlers
from app.models.analytics import (
    AssessmentUnavailable,
    CohortSizeTooLow,
    ComparisonRequest,
    ComparisonResponse,
    NoRankAvailable,
)
from app.models.errors import ErrorEnvelope, ValidationErrorEnvelope
from app.models.feedback import FeedbackRequest
from app.models.onboarding import OnboardingRequest
from app.models.speech_assessment import (
    SpeechAssessmentRequest,
    SpeechAssessmentResponse,
)
from app.models.user_requests import DeleteUserRequest
from app.models.users import SetUserCEFRLevelRequest, SetUserCEFRLevelResponse
from app.services.admin_service import delete_user
from app.services.analytics_service import get_comparison
from app.services.feedback_service import record_feedback
from app.services.onboarding_service import create_onboarding_user
from app.services.speech_assessment_service import assess_speech_request
from app.services.user_service import update_user_cefr_level
from app.utils.logger import configure_app_logging, get_logger

configure_app_logging(SETTINGS.logs_save_dir, SETTINGS.log_level)
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


# Every error except 422 uses this envelope; 422 adds an `errors` array. Declared
# once here and referenced per endpoint so generated clients can parse failures.
ERROR_RESPONSE = {"model": ErrorEnvelope}
VALIDATION_RESPONSE = {"model": ValidationErrorEnvelope}

# Set root_path to /api/v1 to ensure correct routing when behind a reverse proxy with a base path.
app = FastAPI(
    lifespan=lifespan,
    root_path="/api/v1",
    title="DTA Server",
    # The release version, baked into the image by CI. Clients feature-detect
    # against /status's `version`, which serves the same value.
    version=SETTINGS.server_version,
    responses={422: VALIDATION_RESPONSE},
)
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
            # The release (e.g. "1.2.0"), for client feature detection -- the
            # same value as OpenAPI info.version. "0.0.0-dev" outside CI builds.
            "version": SETTINGS.server_version,
            "uptime_seconds": uptime_seconds,
        },
        status_code=200,
    )


@app.post(
    "/analytics/comparison",
    response_model=(ComparisonResponse | AssessmentUnavailable
                    | CohortSizeTooLow | NoRankAvailable),
    responses={403: ERROR_RESPONSE, 404: ERROR_RESPONSE},
)
async def analytics_comparison(data: ComparisonRequest = Form()) -> JSONResponse:
    """Return cohort comparison stats for the requesting user.

    The three "not available yet" shapes are also HTTP 200. The contract clients
    rely on: `status` is ALWAYS present on the unavailable shapes and NEVER present
    on a successful comparison -- branch on that, not on sentinel values.

    Args:
        data: Comparison request payload including user GUID and window options.

    Returns:
        JSONResponse: 200 with percentile/rank data or comparison unavailable status.
    """

    return get_comparison(data)


# POST /request/user was removed in v1.3.0. It carried a `type` field with `delete` and
# `export` values; the app never exports user data and never will, and a route that accepts
# the request is a route that can be built out. Deletion is DELETE /users below -- one
# route, one credential, and an honest status code instead of a 202 whose body had to be
# read to find out whether anything happened.


@app.post(
    "/feedback",
    status_code=201,
    responses={403: ERROR_RESPONSE, 404: ERROR_RESPONSE, 409: ERROR_RESPONSE},
)
async def feedback(data: FeedbackRequest = Form()) -> JSONResponse:
    """Submit assessment or experience feedback.

    `assessment_id` is REQUIRED for the assessment-scoped types (`self_assessment`,
    `result_accuracy`, `result_understanding`) and MUST BE OMITTED -- genuinely
    absent, not 0 or null -- for the app-scoped types (`comparison_ui`,
    `overall_experience`). Violations return 422.

    Args:
        data: Feedback payload including classification, score, and optional comment.

    Returns:
        JSONResponse: 201 when feedback is accepted.
    """

    return record_feedback(data)


@app.post(
    "/speech/assess",
    response_model=SpeechAssessmentResponse,
    responses={
        400: ERROR_RESPONSE, 403: ERROR_RESPONSE, 404: ERROR_RESPONSE,
        413: ERROR_RESPONSE, 415: ERROR_RESPONSE, 503: ERROR_RESPONSE,
    },
)
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


@app.post(
    "/onboarding",
    status_code=201,
    responses={409: ERROR_RESPONSE},
)
async def onboarding(data: OnboardingRequest = Form()) -> Response:
    """Create a new user from onboarding form data.

    Only `guid`, `consent_accepted`, `consent_timestamp` and
    `finnish_self_assessment` are required. Everything else is research metadata:
    omitted or empty fields store null and never block account creation. Unknown
    form fields are ignored, so a newer app can send questions this server does
    not know yet.

    Args:
        data: Onboarding payload containing profile and language background fields.

    Returns:
        Response: 201 when the user is created.
    """

    return create_onboarding_user(data)


@app.patch(
    "/users/level",
    status_code=200,
    response_model=SetUserCEFRLevelResponse,
    responses={403: ERROR_RESPONSE, 404: ERROR_RESPONSE},
)
async def set_user_level(
    data: SetUserCEFRLevelRequest = Depends(SetUserCEFRLevelRequest.as_form)
) -> JSONResponse:
    """Move the CEFR level a user is working at, from the profile screen.

    Its own path, deliberately not `PATCH /users`: `/users` erases the account, and on the
    client every endpoint is a full URL in one config file. Two constants holding the same
    string, told apart only by a verb chosen elsewhere, is a mistake waiting to happen in
    the direction nobody wants.

    The onboarding self-assessment is never changed by this route -- it records what the
    learner believed at sign-up. Cohort ranking in /analytics/comparison follows the value
    set here.

    Args:
        data: Target level (not a direction) and the guid it applies to.

    Returns:
        JSONResponse: 200 with the level as stored.
    """

    return update_user_cefr_level(data)


@app.delete(
    "/users",
    status_code=204,
    responses={403: ERROR_RESPONSE, 500: ERROR_RESPONSE},
)
async def delete_users(
    data: DeleteUserRequest = Depends(
        DeleteUserRequest.as_form)
) -> Response:
    """Delete all persisted data for a user.

    Args:
        data: Delete payload containing target GUID and admin API key.

    Returns:
        Response: 204 when deletion succeeds.
    """

    return delete_user(data)
