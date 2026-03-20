from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

Score = Annotated[float, Field(ge=0, le=5)]


class SpeechAssessmentRequest(BaseModel):
    """Speech assessment request type.

    Attributes:
        audio_file: The path to the audio file to be assessed.
        guid: A unique identifier for the user.
    """

    # pylint: disable=fixme
    # TODO: Move audio file validation here instead of in the endpoint,
    #  and ensure it is a valid WAV file.

    audio_file: str
    guid: UUID


class SpeechAssessmentScores(BaseModel):
    """Speech assessment score type."""

    accuracy: Score
    fluency: Score
    proficiency: Score
    pronunciation: Score
    range: Score


class SpeechAssessmentResponse(BaseModel):
    """Speech assessment response type.

    Attributes:
        scores: The individual scores for different aspects of speech.
        transcript: The transcribed text of the user's speech.
    """

    scores: SpeechAssessmentScores
    transcript: str
