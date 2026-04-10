import os
import tempfile
from random import uniform

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.models.speech_assessment import (
    SpeechAssessmentRequest,
    SpeechAssessmentResponse,
    SpeechAssessmentScores,
)
from app.utils.whisper_model import get_transcriber
from app.validators import audio, auth


async def assess_speech_request(
    data: SpeechAssessmentRequest,
) -> JSONResponse:
    """Validate, transcribe, and score uploaded speech for an authenticated user."""

    auth.validate_user_access(data.guid)
    content = await audio.validate_file_size(data.file)
    audio.validate_wav_headers(content)

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav", prefix="dta_") as f:
            f.write(content)
            temp_path = f.name

        os.chmod(temp_path, 0o600)
        audio.validate_wav_structure(temp_path)
        audio.validate_audio_duration(temp_path)

        transcribe = get_transcriber()
        result = transcribe(temp_path, language="fi")
        text = result["text"]
        transcript = " ".join(text) if isinstance(text, list) else str(text)

        results = SpeechAssessmentResponse(
            scores=SpeechAssessmentScores(
                accuracy=round(uniform(0, 5), 1),
                fluency=round(uniform(0, 5), 1),
                proficiency=round(uniform(0, 5), 1),
                pronunciation=round(uniform(0, 5), 1),
                range=round(uniform(0, 5), 1),
            ),
            transcript=transcript,
        )
        return JSONResponse(content=jsonable_encoder(results), status_code=200)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
