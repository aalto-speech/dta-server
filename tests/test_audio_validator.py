import asyncio
import wave
from pathlib import Path

import pytest

from app.error_handlers import AppError, ErrorType
from app.validators import audio


def test_validate_file_extension_accepts_wav() -> None:
    """Positive case: .wav extension (case-insensitive) is accepted."""

    audio.validate_file_extension("song.WAV")
    audio.validate_file_extension("subdir/sample.wav")


def test_validate_content_type_accepts_allowed_types() -> None:
    """Allowed MIME types should not raise an error."""

    class FakeFile:
        def __init__(self, content_type: str) -> None:
            self.content_type = content_type

    for ct in audio.ALLOWED_CONTENT_TYPES:
        audio.validate_content_type(FakeFile(ct))


def test_validate_file_size_accepts_exact_limit() -> None:
    """Exactly `MAX_FILE_SIZE` bytes should be accepted."""

    chunk = b"x" * 65536
    chunks = [chunk] * (audio.MAX_FILE_SIZE // 65536)

    class FakeUpload:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        async def read(self, n: int) -> bytes:  # noqa: ARG002
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    content = asyncio.run(audio.validate_file_size(FakeUpload(chunks)))
    assert len(content) == audio.MAX_FILE_SIZE


def test_validate_file_size_many_small_chunks_accept() -> None:
    """Many small chunks that sum to the limit should be accepted."""

    chunk_size = 8192
    num_chunks = audio.MAX_FILE_SIZE // chunk_size
    chunks = [b"x" * chunk_size] * num_chunks

    class FakeUploadMany:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        async def read(self, n: int) -> bytes:  # noqa: ARG002
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    content = asyncio.run(audio.validate_file_size(FakeUploadMany(chunks)))
    assert len(content) == audio.MAX_FILE_SIZE


def test_validate_audio_duration_accepts_exact_limit(monkeypatch, tmp_path: Path) -> None:
    """Duration exactly equal to the limit is accepted (no exception)."""

    p = tmp_path / "sample.wav"
    p.write_bytes(b"")

    def _fake_load(_path):
        sample_rate = 16000
        samples = audio.MAX_AUDIO_DURATION * sample_rate

        class FakeWave:
            shape = (1, samples)

        return FakeWave(), sample_rate

    monkeypatch.setattr("app.validators.audio.torchaudio.load", _fake_load)
    # should not raise
    audio.validate_audio_duration(p)


def test_validate_audio_duration_rejects_over(monkeypatch, tmp_path: Path) -> None:
    """Duration exceeding the limit by one sample should be rejected."""

    p = tmp_path / "sample.wav"
    p.write_bytes(b"")

    def _fake_load(_path):
        sample_rate = 16000
        samples = audio.MAX_AUDIO_DURATION * sample_rate + 1

        class FakeWave:
            shape = (1, samples)

        return FakeWave(), sample_rate

    monkeypatch.setattr("app.validators.audio.torchaudio.load", _fake_load)

    with pytest.raises(AppError) as err:
        audio.validate_audio_duration(p)

    assert err.value.status_code == 413


def test_validate_wav_structure_accepts_minimal_wav(tmp_path: Path) -> None:
    """Create a small valid WAV and ensure the structure validator accepts it."""

    p = tmp_path / "good.wav"

    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00" * 8000 * 2)

    # should not raise
    audio.validate_wav_structure(p)


def test_validate_content_type_rejects_unsupported() -> None:
    class FakeFile:
        content_type = "text/plain"

    with pytest.raises(AppError) as err:
        audio.validate_content_type(FakeFile())

    assert err.value.status_code == 415
    assert err.value.error_type == ErrorType.UNSUPPORTED_MEDIA_TYPE
    assert "Unsupported media type" in err.value.message


def test_validate_file_extension_rejects_non_wav() -> None:
    with pytest.raises(AppError) as err:
        audio.validate_file_extension("song.mp3")

    assert err.value.status_code == 400
    assert err.value.error_type == ErrorType.BAD_REQUEST
    assert err.value.message == "Filename must have a .wav extension."


def test_validate_file_size_empty() -> None:
    class FakeUpload:
        async def read(self, n: int) -> bytes:  # noqa: ARG002
            return b""

    with pytest.raises(AppError) as err:
        asyncio.run(audio.validate_file_size(FakeUpload()))

    assert err.value.status_code == 400
    assert err.value.error_type == ErrorType.BAD_REQUEST
    assert "Uploaded file is empty." in err.value.message


def test_validate_file_size_exceeds() -> None:
    half = audio.MAX_FILE_SIZE // 2 + 1

    class FakeUpload:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        async def read(self, n: int) -> bytes:  # noqa: ARG002
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

    fa = FakeUpload([b"x" * half, b"x" * half])

    with pytest.raises(AppError) as err:
        asyncio.run(audio.validate_file_size(fa))

    assert err.value.status_code == 413
    assert err.value.error_type == ErrorType.FILE_TOO_LARGE


def test_validate_wav_headers_too_small() -> None:
    with pytest.raises(AppError) as err:
        audio.validate_wav_headers(b"short")

    assert err.value.status_code == 400
    assert err.value.message == "File too small to be a valid WAV."


def test_validate_wav_headers_invalid_magic() -> None:
    with pytest.raises(AppError) as err:
        audio.validate_wav_headers(b"X" * 12)

    assert err.value.status_code == 400
    assert err.value.message == "File does not have valid WAV headers."


def test_validate_wav_structure_raises_on_corrupt_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")

    with pytest.raises(AppError) as err:
        audio.validate_wav_structure(bad)

    assert err.value.status_code == 400


def test_validate_audio_duration_metadata_error(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "sample.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 64 + b"WAVE")

    def _raise_on_load(_path):
        raise RuntimeError("torchaudio failed")

    monkeypatch.setattr("app.validators.audio.torchaudio.load", _raise_on_load)

    with pytest.raises(AppError) as err:
        audio.validate_audio_duration(p)

    assert err.value.status_code == 400


def test_validate_audio_duration_exceeds(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "sample.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 64 + b"WAVE")

    def _fake_load(path):
        sample_rate = 16000
        samples = (audio.MAX_AUDIO_DURATION + 1) * sample_rate

        class FakeWave:
            shape = (1, samples)

        return FakeWave(), sample_rate

    monkeypatch.setattr("app.validators.audio.torchaudio.load", _fake_load)

    with pytest.raises(AppError) as err:
        audio.validate_audio_duration(p)

    assert err.value.status_code == 413
