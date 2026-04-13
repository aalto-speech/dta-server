# pylint: disable=redefined-outer-name

import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


def _valid_form_data(**overrides):
    data = {
        "guid": str(uuid4()),
        "task_id": "1",
    }
    data.update(overrides)
    return data


def _valid_wav_bytes() -> bytes:
    # Minimal bytes that satisfy RIFF/WAVE magic header checks.
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + (b"\x00" * 32)


def test_assess_speech_success_returns_scores_and_transcript(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /speech/assess happy path returns expected payload and status."""

    captured = {}

    async def _fake_validate_file_size(_):
        return _valid_wav_bytes()

    def _fake_validate_user_access(_):
        return None

    def _record_wav_structure(path: str):
        captured["temp_path"] = path
        assert os.path.exists(path)

    def _fake_transcribe(*_args, **_kwargs):
        "returns fake transcribed text"
        return {"text": ["Hei", "maailma"]}

    monkeypatch.setattr("app.services.speech_assessment_service.auth.validate_user_access",
                        _fake_validate_user_access)
    monkeypatch.setattr("app.services.speech_assessment_service.audio.validate_file_size",
                        _fake_validate_file_size)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.audio.validate_wav_structure", _record_wav_structure)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.audio.validate_audio_duration", lambda _path: None)
    monkeypatch.setattr("app.services.speech_assessment_service.get_transcriber",
                        lambda: _fake_transcribe)
    monkeypatch.setattr("app.services.speech_assessment_service.uniform",
                        lambda _a, _b: 2.5)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.create_assessment", lambda _data: 1
    )

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assessment_id"] == 1
    assert payload["scores"] == {
        "accuracy": 2.5,
        "fluency": 2.5,
        "proficiency": 2.5,
        "pronunciation": 2.5,
        "range": 2.5,
    }
    assert payload["transcript"] == "Hei maailma"
    assert "temp_path" in captured
    assert os.path.exists(captured["temp_path"])
    os.unlink(captured["temp_path"])


def test_assess_speech_rejects_invalid_extension(client: TestClient):
    """Test /speech/assess returns 400 for non-.wav filename."""

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.mp3", b"ignored", "audio/wav")},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Filename must have a .wav extension."}


def test_assess_speech_rejects_invalid_content_type(client: TestClient):
    """Test /speech/assess returns 415 for unsupported content type."""

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.wav", b"ignored", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"].startswith(
        "Unsupported media type: expected a WAV file")


def test_assess_speech_rejects_invalid_guid_format(client: TestClient):
    """Test /speech/assess returns 422 for invalid GUID."""

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(guid="not-a-guid"),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 422


def test_assess_speech_rejects_non_integer_task_id(client: TestClient):
    """Test /speech/assess returns 422 when task_id is not an integer."""

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(task_id="task-1"),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 422


def test_assess_speech_stops_before_file_processing_when_auth_fails(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test auth failure short-circuits before file validation/processing."""

    called = {"validate_file_size": False}

    def _deny_access(_):
        raise HTTPException(status_code=404, detail="User not found")

    async def _fake_validate_file_size(_):
        called["validate_file_size"] = True
        return _valid_wav_bytes()

    monkeypatch.setattr(
        "app.services.speech_assessment_service.auth.validate_user_access", _deny_access)
    monkeypatch.setattr("app.services.speech_assessment_service.audio.validate_file_size",
                        _fake_validate_file_size)

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}
    assert called["validate_file_size"] is False


def test_assess_speech_keeps_audio_file_on_unhandled_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test persisted audio file remains if transcription raises an exception."""

    captured = {}

    async def _fake_validate_file_size(_):
        return _valid_wav_bytes()

    def _fake_validate_user_access(_):
        return None

    def _record_wav_structure(path: str):
        captured["temp_path"] = path
        assert os.path.exists(path)

    def _fake_transcribe_fails(*_args, **_kwargs):
        "fake transcription that always fails"
        raise RuntimeError("transcription failure")

    monkeypatch.setattr("app.services.speech_assessment_service.auth.validate_user_access",
                        _fake_validate_user_access)
    monkeypatch.setattr("app.services.speech_assessment_service.audio.validate_file_size",
                        _fake_validate_file_size)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.audio.validate_wav_structure", _record_wav_structure)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.audio.validate_audio_duration", lambda _path: None)
    monkeypatch.setattr("app.services.speech_assessment_service.get_transcriber",
                        lambda: _fake_transcribe_fails)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/speech/assess",
            data=_valid_form_data(),
            files={"file": ("sample.wav", b"ignored", "audio/wav")},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "temp_path" in captured
    assert os.path.exists(captured["temp_path"])
    os.unlink(captured["temp_path"])
