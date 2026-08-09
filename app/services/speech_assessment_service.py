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

        # Three different situations hide behind one 503, and the client needs to
        # word (and pace) its retry differently for each -- see docs/FRONTEND.md.
        if err.timed_out:
            reason, retry_after = "busy", 60
            message = "Speech scoring is busy. Please try again shortly."
        elif err.status == 503:
            # The inference container answers but its model is still loading.
            reason, retry_after = "starting_up", 30
            message = "Speech scoring is starting up. Please try again in a moment."
        else:
            # Connection refused / DNS / a non-503 error: retrying will not help
            # until someone fixes the scorer, so no Retry-After is promised.
            reason, retry_after = "unreachable", None
            message = "Speech scoring is unavailable right now. Please try again later."

        raise AppError(
            status_code=503,
            error_type=ErrorType.SCORING_UNAVAILABLE,
            message=message,
            extra={"reason": reason},
            headers={"Retry-After": str(retry_after)} if retry_after else None,
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

    # Side channel, never a gate: the score above is returned whatever this says, and an
    # older inference image simply omits it. Stored alongside the scores so the study can
    # filter answers that did not address the task.
    content = result.get("content")
    if content:
        logger.info("Content relevance for user %s: %s (%.2f)",
                    data.guid, content["relevance"], content["confidence"])

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
        content_relevance=content["relevance"] if content else None,
        content_confidence=content["confidence"] if content else None,
    ))

    # ? Enhance error handling?
    if not assessment_id:
        raise sqlite3.DatabaseError(
            "Failed to create assessment record in the database")

    logger.info("Stored speech assessment %s for user %s",
                assessment_id, data.guid)

    results = SpeechAssessmentResponse(
        assessment_id=assessment_id,
        # Echo of the id that was actually scored, so the client can assert it matches
        # what it sent -- a mis-wired task otherwise produces a plausible wrong score
        # with no error anywhere.
        task_id=data.task_id,
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
        dimension_labels=result["dimension_labels"],
        clipped=result["clipped"],
        content=content,
    )
    return JSONResponse(content=jsonable_encoder(results), status_code=200)
