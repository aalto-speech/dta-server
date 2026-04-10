from enum import StrEnum
from uuid import UUID

from fastapi import Form, Header
from pydantic import BaseModel


class RequestType(StrEnum):
    """User request types."""

    DELETE = "delete"
    EXPORT = "export"


class UserDataRequest(BaseModel):
    """User data request payload.

    Attributes:
        guid: The user's GUID.
        type: The request type.
    """

    guid: UUID
    type: RequestType


class RequestToDeleteUserForm(BaseModel):
    """Form payload for deleting user data."""

    guid: UUID


class DeleteUserRequest(BaseModel):
    """Admin delete-user request payload.

    Attributes:
        api_key: Admin API key.
        guid: The user's GUID.
    """

    api_key: str
    guid: UUID

    @classmethod
    def as_form(
        cls,
        x_api_key: str = Header(..., alias="X-API-Key"),
        guid: UUID = Form(...),
    ) -> "DeleteUserRequest":
        """Build the model from form fields and a header."""

        return cls(guid=guid, api_key=x_api_key)


class CreateUserRequestInput(BaseModel):
    """Internal DB input for creating a user request row."""

    guid: UUID
    type: RequestType


class DeleteUserDataInput(BaseModel):
    """Internal DB input for deleting all data for a user."""

    guid: UUID


class GetUserInput(BaseModel):
    """Internal DB input for checking user existence."""

    guid: UUID


class GetUserConsentInput(BaseModel):
    """Internal DB input for checking user consent state."""

    guid: UUID
