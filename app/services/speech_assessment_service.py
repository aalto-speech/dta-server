import os
from pathlib import Path
from random import uniform
import sqlite3
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.config import SETTINGS
from app.db import create_assessment
from app.models.speech_assessment import (
    AssessmentCreateInput,
    SpeechAssessmentRequest,
    SpeechAssessmentResponse,
    SpeechAssessmentScores,
)
from app.utils.logger import get_logger
from app.utils.whisper_model import get_transcriber
from app.validators import audio, auth


logger = get_logger(__name__)


def _create_audio_path(guid: UUID) -> tuple[UUID, Path]:
    output_dir = os.path.join(SETTINGS.audio_save_dir, str(guid))
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    audio_id = uuid4()
    audio_path = Path(os.path.join(output_dir, f"{audio_id}.wav"))
    return audio_id, audio_path


def _transcribe(audio_path: Path) -> str:
    """Transcribe audio with Whisper.

    Args:
        audio_path: Path to the audio file.

    Returns:
        str: The transcribed text.
    """

    ts = get_transcriber()
    result = ts(str(audio_path), language="fi")
    text = result["text"]
    transcript = " ".join(text) if isinstance(text, list) else str(text)
    return transcript


async def assess_speech_request(
    data: SpeechAssessmentRequest,
) -> JSONResponse:
    """Validate, transcribe, and score uploaded speech for an authenticated user.

    Args:
        data: Speech assessment form payload with user GUID and WAV file.

    Returns:
        JSONResponse: 200 with generated scores and transcription result.
    """

    auth.validate_user_access(data.guid)
    content = await audio.validate_file_size(data.file)
    audio.validate_wav_headers(content)

    audio_id, audio_path = _create_audio_path(data.guid)

    with open(audio_path, "wb") as f:
        f.write(content)

    os.chmod(audio_path, 0o600)
    audio.validate_wav_structure(audio_path)
    audio.validate_audio_duration(audio_path)

    # Transcribe the audio using the Whisper model
    transcript = _transcribe(audio_path)

    accuracy = round(uniform(0, 5), 1)
    fluency = round(uniform(0, 5), 1)
    proficiency = round(uniform(0, 5), 1)
    pronunciation = round(uniform(0, 5), 1)
    range_score = round(uniform(0, 5), 1)

    assessment_id = create_assessment(AssessmentCreateInput(
        guid=data.guid,
        task_id=data.task_id,
        audio_id=audio_id,
        audio_path=audio_path,
        transcript=transcript,
        accuracy=accuracy,
        fluency=fluency,
        proficiency=proficiency,
        pronunciation=pronunciation,
        range_score=range_score,
    ))

    # ? Enhance error handling?
    if not assessment_id:
        raise sqlite3.DatabaseError(
            "Failed to create assessment record in the database")

    logger.info("Stored speech assessment %s for user %s",
                assessment_id, data.guid)

    results = SpeechAssessmentResponse(
        assessment_id=assessment_id,
        scores=SpeechAssessmentScores(
            accuracy=accuracy,
            fluency=fluency,
            proficiency=proficiency,
            pronunciation=pronunciation,
            range=range_score,
        ),
        transcript=transcript,
    )
    return JSONResponse(content=jsonable_encoder(results), status_code=200)
