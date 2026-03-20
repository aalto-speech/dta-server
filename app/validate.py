import os
import wave

import torchaudio
from fastapi import HTTPException, UploadFile

# Maximum allowed upload size: 10 MB
MAX_FILE_SIZE = 10 * 1024 * 1024

# Maximum allowed audio duration in seconds
MAX_AUDIO_DURATION = 90

# MIME type accepted for WAV audio uploads
ALLOWED_CONTENT_TYPES = {"application/octet-stream", "audio/wav"}

# WAV files start with "RIFF" at offset 0 and "WAVE" at offset 8
WAV_RIFF_MAGIC = b"RIFF"
WAV_WAVE_MAGIC = b"WAVE"

# $ FEEDBACK
# feedback target_types
FEEDBACK_TARGET_TYPES = ("assessment", "rating_ui",
                         "comparison_ui", "general_experience")
MAX_COMMENT_LENGTH = 500


def _validate_feedback(guid: str, assessment_id: int | None, target_type: str,
                       reaction_value: int, comment: str | None) -> None:
    """Validate feedback form data before saving to database.

    Raises:
        HTTPException (400): If any field fails validation.
    """
    if not guid or not isinstance(guid, str):
        raise HTTPException(
            status_code=400, detail="guid is required and must be a string.")

    if target_type not in FEEDBACK_TARGET_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"target_type must be one of {FEEDBACK_TARGET_TYPES}."
        )

    if not isinstance(reaction_value, int) or reaction_value < 1 or reaction_value > 5:
        raise HTTPException(
            status_code=400,
            detail="reaction_value must be an integer between 1 and 5."
        )

    if assessment_id is not None and (not isinstance(assessment_id, int) or assessment_id < 0):
        raise HTTPException(
            status_code=400, detail="assessment_id must be a non-negative integer.")

    if comment is not None:
        if not isinstance(comment, str):
            raise HTTPException(
                status_code=400, detail="comment must be a string if provided.")
        if len(comment) > MAX_COMMENT_LENGTH:
            raise HTTPException(
                status_code=400, detail=f"comment must not exceed {MAX_COMMENT_LENGTH} characters.")


def _validate_content_type(file: UploadFile) -> None:
    """Reject uploads whose Content-Type is not application/octet-stream.

    Raises:
        HTTPException (415): If the Content-Type is not application/octet-stream.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: expected a WAV file, "
            f"got '{file.content_type}'.",
        )


def _validate_file_name(filename: str) -> None:
    """Sanitise the filename and ensure it has a .wav extension.

    Directory components are stripped to prevent path-traversal attacks.

    Raises:
        HTTPException (400): If the filename does not end with .wav.
    """
    safe_name = os.path.basename(filename)
    if not safe_name.lower().endswith(".wav"):
        raise HTTPException(
            status_code=400, detail="Filename must have a .wav extension."
        )


async def _validate_file_size(file: UploadFile) -> bytes:
    """Stream the upload in 64 KB chunks and enforce the size limit.

    Reading in chunks prevents an attacker from forcing the server to
    buffer an arbitrarily large file in memory.

    Returns:
        The complete file content as bytes.

    Raises:
        HTTPException (413): If the file exceeds MAX_FILE_SIZE.
        HTTPException (400): If the file is empty.
    """
    chunks: list[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(1024 * 64)  # 64 KB chunks
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail="File exceeds the 10 MB size limit.",
            )
        chunks.append(chunk)

    if total_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    return b"".join(chunks)


def _validate_wav_headers(data: bytes) -> None:
    """Validate that raw bytes begin with the RIFF/WAVE magic bytes.

    This guards against files that claim to be .wav via extension or
    Content-Type but contain a different (potentially malicious) payload.

    Raises:
        HTTPException (400): If the file is too small to be a valid WAV.
        HTTPException (400): If the file does not have valid WAV headers.
    """
    if len(data) < 12:
        raise HTTPException(
            status_code=400, detail="File too small to be a valid WAV.")
    if data[:4] != WAV_RIFF_MAGIC or data[8:12] != WAV_WAVE_MAGIC:
        raise HTTPException(
            status_code=400, detail="File does not have valid WAV headers.")


def _validate_wav_structure(path: str) -> None:
    """Open the file with the stdlib `wave` module to confirm it is a
    structurally valid WAV file (correct chunks, sample params, etc.).

    Raises:
        HTTPException (400): If the uploaded file is not a valid WAV file.
    """
    try:
        with wave.open(path, "rb") as wf:
            # Reading basic params forces the parser to walk the header
            wf.getparams()
    except wave.Error as err:
        raise HTTPException(
            status_code=400, detail="Uploaded file is not a valid WAV file."
        ) from err


def _validate_audio_duration(path: str) -> None:
    """Reject audio files longer than MAX_AUDIO_DURATION seconds.

    Uses torchaudio.load to obtain the waveform length and sample rate.

    Raises:
        HTTPException (400): If torchaudio cannot read the file.
        HTTPException (413): If the audio exceeds MAX_AUDIO_DURATION.
    """
    try:
        waveform, sample_rate = torchaudio.load(path)
    except Exception as err:
        raise HTTPException(
            status_code=400,
            detail="Could not read audio metadata.",
        ) from err

    duration = waveform.shape[1] / sample_rate
    if duration > MAX_AUDIO_DURATION:
        raise HTTPException(
            status_code=413,
            detail=f"Audio exceeds the {MAX_AUDIO_DURATION}s duration limit.",
        )
