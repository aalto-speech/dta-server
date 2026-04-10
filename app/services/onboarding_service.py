from fastapi.responses import Response

from app.db import create_user
from app.models.onboarding import OnboardingRequest


def create_onboarding_user(data: OnboardingRequest) -> Response:
    """Create a user from onboarding payload."""

    create_user(data)
    return Response(status_code=201)
