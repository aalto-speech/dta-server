from enum import StrEnum
from uuid import UUID

from fastapi import Form, Header
from pydantic import BaseModel


class RequestType(StrEnum):
    """Enumeration of user request types."""

    DELETE = "delete"
    EXPORT = "export"


class UserDataRequest(BaseModel):
    """User data request type for database storage.

    Attributes:
        guid: A unique identifier for the user.
        type: The type of request (e.g., delete, export).
    """

    guid: UUID
    type: RequestType


class RequestToDeleteUserForm(BaseModel):
    """User data deletion request type for form parsing.

    Attributes:
        guid: A unique identifier for the user.
    """

    guid: UUID


class DeleteUserRequest(BaseModel):
    """Delete user data request type for admin use.

    Attributes:
        api_key: Admin API key for authorization.
        guid: A unique identifier for the user whose data should be deleted.
    """

    api_key: str
    guid: UUID

    @classmethod
    def as_form(
        cls,
        x_api_key: str = Header(..., alias="X-API-Key"),
        guid: UUID = Form(...),
    ) -> "DeleteUserRequest":
        """Build model instance from form field and header."""

        return cls(guid=guid, api_key=x_api_key)
