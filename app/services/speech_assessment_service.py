import os
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.config import SETTINGS
from app.db import create_assessment
from app.error_handlers import AppError, ErrorType
from app.models.speech_assessment import (
    AssessmentCreateInput,
    SpeechAssessmentRequest,
    SpeechAssessmentResponse,
    SpeechAssessmentScores,
)
from app.utils.asa_client import ASAClient, ASAError
from app.utils.logger import get_logger
from app.utils.storage import user_audio_dir
from app.validators import audio, auth


logger = get_logger(__name__)

# One client for the whole app; the inference service serialises scoring anyway.
_asa = ASAClient(base_url=SETTINGS.asa_url, timeout=SETTINGS.asa_timeout)


def _create_audio_path(guid: UUID) -> tuple[UUID, Path]:
    output_dir = user_audio_dir(guid)
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    audio_id = uuid4()
    audio_path = output_dir / f"{audio_id}.wav"
    return audio_id, audio_path


async def _score(content: bytes, data: SpeechAssessmentRequest) -> dict:
    """Send audio to the M-CASA inference service and map failures to AppError."""

    try:
        return await _asa.assess(
            content,
            task_id=data.task_id,
            filename=data.file.filename or "audio.wav",
        )
    except ASAError as err:
        if err.status == 404:
            # The service refuses unmapped ids rather than scoring the wrong task.
            raise AppError(
                status_code=400,
                error_type=ErrorType.BAD_REQUEST,
                message=f"Unknown task_id {data.task_id}.",
            ) from err

        logger.error("ASA scoring failed for user %s: %s", data.guid, err)
        raise AppError(
            status_code=503,
            error_type=ErrorType.SCORING_UNAVAILABLE,
            message="Speech scoring is temporarily unavailable. Please try again shortly.",
        ) from err


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

    result = await _score(content, data)

    transcript = result["transcript"]
    scores = result["scores"]  # keys match the DB columns, except range -> range_score
    accuracy = scores["accuracy"]
    fluency = scores["fluency"]
    proficiency = scores["proficiency"]
    pronunciation = scores["pronunciation"]
    range_score = scores["range"]

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
        cefr_label=result["cefr_label"],
        cefr_label_fine=result["cefr_label_fine"],
        clipped=result["clipped"],
    )
    return JSONResponse(content=jsonable_encoder(results), status_code=200)
