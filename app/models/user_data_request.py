from uuid import UUID

from pydantic import BaseModel


class UserDataDeleteRequest(BaseModel):
    """User data deletion request type.

    Attributes:
        guid: A unique identifier for the user.
    """

    guid: UUID
