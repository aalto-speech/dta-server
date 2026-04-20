from fastapi.responses import Response

from app.db import create_user
from app.models.onboarding import CreateUserInput, OnboardingRequest
from app.utils.logger import get_logger


logger = get_logger(__name__)


def create_onboarding_user(data: OnboardingRequest) -> Response:
    """Create a user from onboarding data.

    Args:
        data: Onboarding payload containing profile and language background fields.

    Returns:
        Response: 201 when the user is created.
    """

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
    logger.info("Created onboarding user %s", data.guid)
    return Response(status_code=201)
