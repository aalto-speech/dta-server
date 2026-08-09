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

# The CEFR label for a score of 0 (the pipeline's own band 0, "below A1"), used when an
# off-topic answer's scores are withheld so labels and numbers cannot disagree.
WITHHELD_LABEL = "<A1"

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


def _withhold_scores(result: dict) -> dict:
    """Zero every score in a scoring result, keeping the transcript.

    Used when the content judge says the answer addressed a different task. Scoring a
    reply to another question is not a measurement of this one, so the numbers are
    withheld rather than reported, in the response and in the database alike.

    This is deliberately NOT silent: `content.relevance` travels in the same response and
    `content_relevance` is written on the same row, so a zero is always attributable --
    and all five scores going to zero together is a pattern no real assessment produces.
    Labels are zeroed with them, because a 0.0 displayed next to "A2" would be worse than
    either alone.

    The transcript survives: it is what the learner actually said, it is the evidence for
    the verdict, and it is the one part of the result that is still true.

    For analysis: `WHERE content_relevance = 'off_topic'` finds these rows. Exclude them
    rather than reading 0.0 as "below A1" -- the model's real output for these recordings
    is not recoverable from the database.
    """

    return result | {
        "scores": {dimension: 0.0 for dimension in result["scores"]},
        "cefr_label": WITHHELD_LABEL,
        "cefr_label_fine": WITHHELD_LABEL,
        "dimension_labels": {
            dimension: {"label": WITHHELD_LABEL, "label_fine": WITHHELD_LABEL}
            for dimension in result["dimension_labels"]
        },
        # A withheld score is not a clipped measurement; it is not a measurement at all.
        "clipped": False,
    }


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

    relevance = result.get("content")
    if relevance:
        logger.info("Content relevance for user %s: %s (%.2f)",
                    data.guid, relevance["relevance"], relevance["confidence"])
        if relevance["relevance"] == "off_topic":
            logger.info("Withholding scores for user %s: answer judged off-topic",
                        data.guid)
            result = _withhold_scores(result)

    scores = result["scores"]  # keys match the DB columns, except range -> range_score

    assessment_id = create_assessment(AssessmentCreateInput(
        guid=data.guid,
        task_id=data.task_id,
        audio_id=audio_id,
        audio_path=audio_path,
        transcript=result["transcript"],
        accuracy=scores["accuracy"],
        fluency=scores["fluency"],
        proficiency=scores["proficiency"],
        pronunciation=scores["pronunciation"],
        range_score=scores["range"],
        content_relevance=relevance["relevance"] if relevance else None,
        content_confidence=relevance["confidence"] if relevance else None,
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
            accuracy=scores["accuracy"],
            fluency=scores["fluency"],
            proficiency=scores["proficiency"],
            pronunciation=scores["pronunciation"],
            range=scores["range"],
        ),
        # Always the real ASR output, including when the scores are withheld.
        transcript=result["transcript"],
        cefr_label=result["cefr_label"],
        cefr_label_fine=result["cefr_label_fine"],
        dimension_labels=result["dimension_labels"],
        clipped=result["clipped"],
        content=relevance,
    )
    return JSONResponse(content=jsonable_encoder(results), status_code=200)
