from uuid import UUID

from fastapi import Form
from pydantic import BaseModel

from app.models.onboarding import CEFRLevel


class SetUserCEFRLevelRequest(BaseModel):
    """Form payload for `PATCH /users/level`.

    Attributes:
        guid: The user's GUID.
        cefr_level: The level the learner wants to work at from now on.
    """

    guid: UUID
    cefr_level: CEFRLevel

    @classmethod
    def as_form(
        cls,
        guid: UUID = Form(...),
        cefr_level: CEFRLevel = Form(...),
    ) -> "SetUserCEFRLevelRequest":
        """Build the model from form fields.

        A TARGET level, never a direction. The client's buttons say Advance and Revert, but
        what travels is `cefr_level=A2`, so a retried or duplicated request lands the user
        in the same place instead of moving them two steps.
        """

        return cls(guid=guid, cefr_level=cefr_level)


class SetUserCEFRLevelResponse(BaseModel):
    """Response for `PATCH /users/level`: the level as stored.

    Returned rather than assumed so the client can settle without a second round trip,
    though the 2.0.0 client re-reads /analytics/comparison anyway.
    """

    guid: UUID
    cefr_level: CEFRLevel


class SetUserCEFRLevelInput(BaseModel):
    """Internal DB input for moving a user's working CEFR level."""

    guid: UUID
    cefr_level: CEFRLevel
