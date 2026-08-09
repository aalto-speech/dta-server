# pylint: disable=redefined-outer-name

import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.error_handlers import AppError, ErrorType
from app.main import app
from app.models.speech_assessment import SpeechAssessmentRequest, SpeechAssessmentScores
from app.utils.asa_client import ASAError


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


def _fake_asa_result() -> dict:
    """A canned ASAClient.assess() return value (to_server_shape output).

    accuracy is deliberately above 5: scores are CEFR 0-6, and this guards the
    widened Score bound end to end.
    """

    return {
        "transcript": "Hei maailma",
        "scores": {
            "proficiency": 2.1,
            "fluency": 2.2,
            "pronunciation": 2.4,
            "range": 1.9,
            "accuracy": 5.6,
        },
        "cefr_label": "A2",
        "cefr_label_fine": "A2",
        "dimension_labels": {
            "fluency": {"label": "A2", "label_fine": "A2"},
            "pronunciation": {"label": "A2", "label_fine": "A2+"},
            "range": {"label": "A1", "label_fine": "A2"},
            "accuracy": {"label": "C1", "label_fine": "C1+"},
        },
        "clipped": False,
        "reportable_range": [1.14, 3.5],
        "proficiency_uncalibrated": 1.98,
        "task": {"task_id": "03_m", "task_name": "dta-task2_a", "model_task_id": 23},
        "audio": {"duration_sec": 27.4, "truncated": False},
        "model_checkpoint": "finnish-v3_le40_mcasa_noac_eqcap2x_ttsfluall3_s2022",
    }


def _patch_happy_path(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    """Stub auth, audio validators and the ASA client for a successful request."""

    async def _fake_validate_file_size(_):
        return _valid_wav_bytes()

    def _record_wav_structure(path: str):
        captured["temp_path"] = path
        assert os.path.exists(path)

    async def _fake_assess(content, task_id, filename="audio.wav", transcript=None):
        captured["assess_args"] = {
            "content": content,
            "task_id": task_id,
            "filename": filename,
            "transcript": transcript,
        }
        return _fake_asa_result()

    monkeypatch.setattr("app.services.speech_assessment_service.auth.validate_user_access",
                        lambda _guid: None)
    monkeypatch.setattr("app.services.speech_assessment_service.audio.validate_file_size",
                        _fake_validate_file_size)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.audio.validate_wav_structure", _record_wav_structure)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.audio.validate_audio_duration", lambda _path: None)
    monkeypatch.setattr(
        "app.services.speech_assessment_service._asa.assess", _fake_assess)


def test_assess_speech_success_returns_scores_and_transcript(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test /speech/assess happy path returns expected payload and status."""

    captured = {}
    logged = []
    form_data = _valid_form_data()

    _patch_happy_path(monkeypatch, captured)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.create_assessment", lambda _data: 1
    )
    monkeypatch.setattr(
        "app.services.speech_assessment_service.logger.info",
        lambda message, *args: logged.append((message, args)),
    )

    response = client.post(
        "/speech/assess",
        data=form_data,
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assessment_id"] == 1
    assert payload["scores"] == {
        "accuracy": 5.6,
        "fluency": 2.2,
        "proficiency": 2.1,
        "pronunciation": 2.4,
        "range": 1.9,
    }
    assert payload["transcript"] == "Hei maailma"
    assert payload["cefr_label"] == "A2"
    assert payload["cefr_label_fine"] == "A2"
    # Echo of the scored task id, so a mis-wired client fails loudly (item 9).
    assert payload["task_id"] == 1
    assert payload["dimension_labels"]["accuracy"] == {
        "label": "C1", "label_fine": "C1+"}
    assert payload["clipped"] is False
    assert captured["assess_args"]["task_id"] == 1
    assert captured["assess_args"]["filename"] == "sample.wav"
    assert "temp_path" in captured
    assert os.path.exists(captured["temp_path"])
    assert logged == [
        ("Stored speech assessment %s for user %s",
         (1, UUID(form_data["guid"]))),
    ]
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
        "detail": {
            "type": "BAD_REQUEST",
            "message": "Filename must have a .wav extension.",
        }
    }


def test_assess_speech_rejects_invalid_content_type(client: TestClient):
    """Test /speech/assess returns 415 for unsupported content type."""

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.wav", b"ignored", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["type"] == "UNSUPPORTED_MEDIA_TYPE"
    assert response.json()["detail"]["message"].startswith(
        "Unsupported media type: expected a WAV file")


def test_assess_speech_rejects_invalid_guid_format(client: TestClient):
    """Test /speech/assess returns 422 for invalid GUID."""

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(guid="not-a-guid"),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "VALIDATION_ERROR"
    assert response.json()["detail"]["message"] == "Invalid request payload"
    assert isinstance(response.json()["detail"]["errors"], list)


def test_assess_speech_rejects_non_integer_task_id(client: TestClient):
    """Test /speech/assess returns 422 when task_id is not an integer."""

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(task_id="task-1"),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "VALIDATION_ERROR"
    assert response.json()["detail"]["message"] == "Invalid request payload"
    assert isinstance(response.json()["detail"]["errors"], list)


def test_assess_speech_stops_before_file_processing_when_auth_fails(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Test auth failure short-circuits before file validation/processing."""

    called = {"validate_file_size": False}

    def _deny_access(_):
        raise AppError(
            status_code=404,
            error_type=ErrorType.USER_NOT_FOUND,
            message="User not found",
        )

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
    assert response.json() == {
        "detail": {
            "type": "USER_NOT_FOUND",
            "message": "User not found",
        }
    }
    assert called["validate_file_size"] is False


def test_assess_speech_returns_503_when_scoring_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """An unreachable/failed inference service maps to 503 SCORING_UNAVAILABLE,
    and the persisted audio file is kept for later inspection."""

    captured = {}
    _patch_happy_path(monkeypatch, captured)

    async def _fail_assess(*_args, **_kwargs):
        raise ASAError("inference service unreachable at http://inference:8000")

    monkeypatch.setattr(
        "app.services.speech_assessment_service._asa.assess", _fail_assess)

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["type"] == "SCORING_UNAVAILABLE"
    assert "temp_path" in captured
    assert os.path.exists(captured["temp_path"])
    os.unlink(captured["temp_path"])


def test_assess_speech_returns_400_for_unmapped_task_id(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """A 404 from the inference service (unmapped task id) maps to 400."""

    captured = {}
    _patch_happy_path(monkeypatch, captured)

    async def _unknown_task(*_args, **_kwargs):
        raise ASAError("scoring failed (404): unknown task", 404, "unknown task")

    monkeypatch.setattr(
        "app.services.speech_assessment_service._asa.assess", _unknown_task)

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(task_id="99"),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "BAD_REQUEST"
    assert "99" in response.json()["detail"]["message"]
    os.unlink(captured["temp_path"])


def test_assess_speech_returns_database_error_when_assessment_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """A failed assessment insert should map to DATABASE_ERROR response."""

    captured = {}
    _patch_happy_path(monkeypatch, captured)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.create_assessment",
        lambda _data: 0,
    )

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "type": "DATABASE_ERROR",
            "message": "Database error",
        }
    }
    assert "temp_path" in captured
    assert os.path.exists(captured["temp_path"])
    os.unlink(captured["temp_path"])


def test_assess_speech_rejects_too_long_description(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """A description longer than allowed should result in 422 from the endpoint."""

    # Prevent file validators from raising so the description validator runs
    monkeypatch.setattr(
        "app.models.speech_assessment.audio.validate_content_type",
        lambda _file: None,
    )
    monkeypatch.setattr(
        "app.models.speech_assessment.audio.validate_file_extension",
        lambda _fn: None,
    )

    long_desc = "x" * 513
    with pytest.raises(ValueError) as ve:
        SpeechAssessmentRequest.validate_description_length(long_desc)

    assert "description must not exceed" in str(ve.value)


def test_speech_assessment_scores_enforce_range():
    """Scores accept the full CEFR 0-6 scale and reject values beyond it."""

    # 6.0 (C2) is valid on the CEFR scale
    scores = SpeechAssessmentScores(
        accuracy=6.0, fluency=1.0, proficiency=1.0, pronunciation=1.0, range=1.0
    )
    assert scores.accuracy == 6.0

    with pytest.raises(ValidationError):
        SpeechAssessmentScores(
            accuracy=6.5, fluency=1.0, proficiency=1.0, pronunciation=1.0, range=1.0
        )


def test_assess_speech_returns_and_stores_content_relevance(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """The relevance verdict is returned to the client and persisted with the row."""

    captured = {}
    stored = {}

    _patch_happy_path(monkeypatch, captured)

    async def _fake_assess_with_content(content, task_id, filename="audio.wav",
                                        transcript=None):
        captured["assess_args"] = {"content": content, "task_id": task_id,
                                   "filename": filename, "transcript": transcript}
        result = _fake_asa_result()
        result["content"] = {
            "relevance": "off_topic",
            "confidence": 0.93,
            "reason": "The answer does not address the task that was asked.",
            "judge": "dta-relevance-v1",
        }
        return result

    def _record(data):
        stored["content_relevance"] = data.content_relevance
        stored["content_confidence"] = data.content_confidence
        return 7

    monkeypatch.setattr(
        "app.services.speech_assessment_service._asa.assess", _fake_assess_with_content)
    monkeypatch.setattr(
        "app.services.speech_assessment_service.create_assessment", _record)

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["content"] == {
        "relevance": "off_topic",
        "confidence": 0.93,
        "reason": "The answer does not address the task that was asked.",
        "judge": "dta-relevance-v1",
    }
    # The verdict must never move a score -- these are the unchanged model outputs.
    assert payload["scores"]["proficiency"] == 2.1
    assert stored == {"content_relevance": "off_topic", "content_confidence": 0.93}
    os.unlink(captured["temp_path"])


def test_assess_speech_omits_content_when_not_checked(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    """Fail-open: no `content` from the scorer means null, and the score still ships."""

    captured = {}
    stored = {}

    _patch_happy_path(monkeypatch, captured)

    def _record(data):
        stored["content_relevance"] = data.content_relevance
        stored["content_confidence"] = data.content_confidence
        return 8

    monkeypatch.setattr(
        "app.services.speech_assessment_service.create_assessment", _record)

    response = client.post(
        "/speech/assess",
        data=_valid_form_data(),
        files={"file": ("sample.wav", b"ignored", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json()["content"] is None
    assert response.json()["scores"]["proficiency"] == 2.1
    assert stored == {"content_relevance": None, "content_confidence": None}
    os.unlink(captured["temp_path"])
