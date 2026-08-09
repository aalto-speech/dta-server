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
    """Maintainer delete-user request payload.

    Attributes:
        delete_key: The server's SERVER_DELETE_KEY.
        guid: The user's GUID.
    """

    delete_key: str
    guid: UUID

    @classmethod
    def as_form(
        cls,
        x_delete_key: str = Header(..., alias="X-Delete-Key"),
        guid: UUID = Form(...),
    ) -> "DeleteUserRequest":
        """Build the model from form fields and a header.

        The header was `X-API-Key` before v1.2.0. Renamed together with the variable it
        is checked against, so there is one name for this credential everywhere; the
        endpoint is maintainer-operated, so nothing in the app has to be updated.
        """

        return cls(guid=guid, delete_key=x_delete_key)


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
