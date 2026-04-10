
from fastapi.responses import Response

from app.db import delete_user_data
from app.models.user_requests import DeleteUserRequest
from app.utils.logger import get_logger
from app.validators import auth

logger = get_logger(__name__)


def delete_user(data: DeleteUserRequest) -> Response:
    """Delete all user data after validating admin access."""

    auth.validate_admin_access(data.api_key)

    delete_user_data(data)
    logger.info("Admin deleted all data for user: %s", data.guid)
    return Response(status_code=204)
