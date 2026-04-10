from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import File, Form, UploadFile
from pydantic import BaseModel, Field, field_validator

from app.validators import audio

Score = Annotated[float, Field(ge=0, le=5)]


class SpeechAssessmentRequest(BaseModel):
    """Speech assessment request payload.

    Attributes:
        file: The uploaded WAV file.
        guid: The user's GUID.
        task_id: The assessment task ID.
        description: The description of the assessment.
    """

    file: UploadFile
    guid: UUID
    task_id: str | None = None
    description: str | None = None

    @field_validator("file")
    @classmethod
    def validate_file(cls, file: UploadFile) -> UploadFile:
        """Validate that the uploaded file is a WAV audio file."""

        audio.validate_content_type(file)
        audio.validate_file_extension(file.filename or "")

        return file

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str | None) -> str | None:
        """Validate the task ID when provided."""

        max_length = 100
        if value is not None and len(value) > max_length:
            raise ValueError(
                f"task_id must not exceed {max_length} characters.")

        return value

    @field_validator("description")
    @classmethod
    def validate_description_length(cls, value: str | None) -> str | None:
        """Validate the description length when provided."""

        max_length = 512
        if value is not None and len(value) > max_length:
            raise ValueError(
                f"description must not exceed {max_length} characters.")

        return value

    @classmethod
    def as_form(
        cls,
        guid: UUID = Form(...),
        file: UploadFile = File(...),
    ) -> "SpeechAssessmentRequest":
        """Build the model from multipart form fields."""

        return cls(guid=guid, file=file)


class SpeechAssessmentScores(BaseModel):
    """Speech assessment scores."""

    accuracy: Score
    fluency: Score
    proficiency: Score
    pronunciation: Score
    range: Score


class SpeechAssessmentResponse(BaseModel):
    """Speech assessment response payload.

    Attributes:
        assessment_id: The created assessment ID.
        scores: The individual scores.
        transcript: The transcribed text.
    """

    assessment_id: int
    scores: SpeechAssessmentScores
    transcript: str


class AssessmentCreateInput(BaseModel):
    """Internal DB input for creating a speech assessment record."""

    guid: UUID
    task_id: str | None = None
    audio_id: UUID
    audio_path: Path
    transcript: str
    accuracy: Score
    fluency: Score
    proficiency: Score
    pronunciation: Score
    range_score: Score
