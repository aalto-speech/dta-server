import sqlite3

from fastapi.responses import Response

from app.db import create_user
from app.error_handlers import AppError, ErrorType
from app.models.onboarding import CreateUserInput, OnboardingRequest
from app.utils.logger import get_logger


logger = get_logger(__name__)


def create_onboarding_user(data: OnboardingRequest) -> Response:
    """Create a user from onboarding data.

    A 409 from this endpoint means the guid is taken and nothing else. `users` has exactly
    one uniqueness constraint -- `guid TEXT PRIMARY KEY` -- and every other integrity rule
    on the table is a CHECK over an enum that Pydantic rejects as 422 before SQL sees it.
    The client mints a fresh guid and retries on 409, so it matters that the advice can
    actually work: a constraint failure unrelated to the guid would send it round three
    times for a reason regenerating cannot fix. If a second unique constraint is ever added
    to `users`, this handler must narrow with it.

    Args:
        data: Onboarding payload containing profile and language background fields.

    Returns:
        Response: 201 when the user is created.

    Raises:
        AppError: 409 when the guid is already registered.
    """

    try:
        create_user(CreateUserInput(
            app_version=data.app_version,
            age_group=data.age_group,
            finnish_learning_duration=data.finnish_learning_duration,
            finnish_self_assessment=data.finnish_self_assessment,
            gender=data.gender,
            moved_to_finland=data.moved_to_finland,
            native_languages=data.native_languages,
            other_languages=data.other_languages,
            consent_accepted=data.consent_accepted,
            consent_timestamp=data.consent_timestamp,
            guid=data.guid,
        ))
    except sqlite3.IntegrityError as err:
        # Logged at info, not error: a v4 collision is not credible, so in practice this is
        # a retried onboarding or a duplicated request -- normal client behaviour, and the
        # guid is what tells the two apart when someone reads the logs.
        logger.info("Onboarding rejected: guid %s is already registered", data.guid)
        raise AppError(
            status_code=409,
            error_type=ErrorType.GUID_ALREADY_REGISTERED,
            message="This guid is already registered.",
        ) from err

    logger.info("Created onboarding user %s", data.guid)
    return Response(status_code=201)
