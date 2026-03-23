from typing import Annotated
from uuid import UUID

from fastapi import File, Form, UploadFile
from pydantic import BaseModel, Field, field_validator

from app.validators import audio

Score = Annotated[float, Field(ge=0, le=5)]


class SpeechAssessmentRequest(BaseModel):
    """Speech assessment request type.

    Attributes:
        file: The path to the audio file to be assessed.
        guid: A unique identifier for the user.
    """

    file: UploadFile
    guid: UUID

    @field_validator("file")
    @classmethod
    def validate_file(cls, file: UploadFile) -> UploadFile:
        """Validate the uploaded file is a WAV audio file and meets constraints."""

        audio.validate_content_type(file)
        audio.validate_file_extension(file.filename or "")

        return file

    @classmethod
    def as_form(
        cls,
        guid: UUID = Form(...),
        file: UploadFile = File(...),
    ) -> "SpeechAssessmentRequest":
        """Build model instance from multipart form fields."""

        return cls(guid=guid, file=file)


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
