import os
import wave

import torchaudio
from fastapi import HTTPException, UploadFile

# Maximum allowed upload size: 10 MB
MAX_FILE_SIZE = 10 * 1024 * 1024

# Maximum allowed audio duration in seconds
MAX_AUDIO_DURATION = 90

# MIME type accepted for WAV audio uploads
ALLOWED_CONTENT_TYPES = {
    "application/octet-stream", "audio/wav", "audio/vnd.wave"}

# WAV files start with "RIFF" at offset 0 and "WAVE" at offset 8
WAV_RIFF_MAGIC = b"RIFF"
WAV_WAVE_MAGIC = b"WAVE"


def validate_content_type(file: UploadFile) -> None:
    """Reject uploads with an unsupported Content-Type."""
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: expected a WAV file, "
            f"got '{file.content_type}'.",
        )


def validate_file_extension(filename: str) -> None:
    """Sanitize the filename and ensure it has a .wav extension."""
    safe_name = os.path.basename(filename)
    if not safe_name.lower().endswith(".wav"):
        raise HTTPException(
            status_code=400, detail="Filename must have a .wav extension."
        )


async def validate_file_size(file: UploadFile) -> bytes:
    """Stream the upload in chunks and enforce the size limit."""
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


def validate_wav_headers(data: bytes) -> None:
    """Validate that raw bytes begin with the RIFF/WAVE magic bytes."""
    if len(data) < 12:
        raise HTTPException(
            status_code=400, detail="File too small to be a valid WAV.")
    if data[:4] != WAV_RIFF_MAGIC or data[8:12] != WAV_WAVE_MAGIC:
        raise HTTPException(
            status_code=400, detail="File does not have valid WAV headers.")


def validate_wav_structure(path: str) -> None:
    """Confirm that the file is structurally valid WAV audio."""
    try:
        with wave.open(path, "rb") as wf:
            # Reading basic params forces the parser to walk the header
            wf.getparams()
    except wave.Error as err:
        raise HTTPException(
            status_code=400, detail="Uploaded file is not a valid WAV file."
        ) from err


def validate_audio_duration(path: str) -> None:
    """Reject audio files longer than the configured maximum."""
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
