from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import File, Form, UploadFile
from pydantic import BaseModel, Field, field_validator

from app.validators import audio

# Scores are CEFR values on a 0-6 scale (0 = below A1, 1 = A1, 2 = A2, 3 = B1,
# 4 = B2, 5 = C1, 6 = C2). The served model's calibrated output is capped at
# 3.5 (B1+); the bound exists so the schema states the true scale, not a limit.
Score = Annotated[float, Field(ge=0, le=6)]


class SpeechAssessmentRequest(BaseModel):
    """Speech assessment request payload.

    Attributes:
        file (UploadFile): The uploaded WAV file.
        guid (UUID): The user's GUID.
        task_id (int): The task ID.
        description (str | None): The description of the assessment.
    """

    file: UploadFile
    guid: UUID
    task_id: int
    description: str | None = None

    @field_validator("file")
    @classmethod
    def validate_file(cls, file: UploadFile) -> UploadFile:
        """Validate that the uploaded file is a WAV audio file."""

        audio.validate_content_type(file)
        audio.validate_file_extension(file.filename or "")

        return file

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
        task_id: int = Form(...),
        description: str | None = Form(default=None),
    ) -> "SpeechAssessmentRequest":
        """Build the model from multipart form fields."""

        return cls(guid=guid, file=file, task_id=task_id, description=description)


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
        assessment_id (int): The created assessment ID.
        scores (SpeechAssessmentScores): The individual scores. Only `proficiency`
            is calibrated; the four dimensions are raw model outputs on the same
            CEFR scale.
        transcript (str): The transcribed text.
        cefr_label (str): Coarse CEFR label for `proficiency` (2.9 -> "A2").
            Prefer showing labels in the UI: "2.1" reads as a mark out of 5.
        cefr_label_fine (str): Half-step CEFR label (2.5 -> "A2+").
        clipped (bool): True when the raw prediction fell outside the calibrator's
            range, so `proficiency` is a boundary value (the model cannot resolve
            above B1+ / 3.5) rather than a measurement.
    """

    assessment_id: int
    scores: SpeechAssessmentScores
    transcript: str
    cefr_label: str
    cefr_label_fine: str
    clipped: bool = False


class AssessmentCreateInput(BaseModel):
    """Internal DB input for creating a speech assessment record."""

    guid: UUID
    task_id: int
    audio_id: UUID
    audio_path: Path
    transcript: str
    accuracy: Score
    fluency: Score
    proficiency: Score
    pronunciation: Score
    range_score: Score
