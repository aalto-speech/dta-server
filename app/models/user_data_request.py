from uuid import UUID

from pydantic import BaseModel


class UserDataDeleteRequest(BaseModel):
    """User data deletion request type"""

    guid: UUID
