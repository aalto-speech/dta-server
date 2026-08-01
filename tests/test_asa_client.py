import pytest

from app.utils.asa_client import ASAError, to_server_shape


def _recorded_payload() -> dict:
    """A real /score response shape (values from a staging run)."""

    return {
        "task": {"task_id": "03_m", "task_name": "dta-task2_a",
                 "corpus": "dta", "model_task_id": 23},
        "transcript": "no mä voisin antaa sulle vähän rahaa",
        "transcript_source": "asr:finnish_v3_whisper_medium",
        "cefr": {
            "score": 2.10,
            "label": "A2",
            "label_fine": "A2",
            "calibration": "isotonic",
            "score_uncalibrated": 1.98,
            "clipped_to_calibration_range": False,
            "reportable_range": [1.14, 3.5],
        },
        "dimensions": {
            "fluency": {"score": 2.21, "label": "A2", "label_fine": "A2", "calibration": None},
            "pronunciation": {"score": 2.35, "label": "A2", "label_fine": "A2+",
                              "calibration": None},
            "range": {"score": 1.88, "label": "A1", "label_fine": "A2", "calibration": None},
            "accuracy": {"score": 1.79, "label": "A1", "label_fine": "A2", "calibration": None},
        },
        "audio": {"duration_sec": 27.4, "n_chunks": 1, "truncated": False, "max_scored_sec": 120},
        "model": {"checkpoint": "finnish-v3_le40_mcasa_noac_eqcap2x_ttsfluall3_s2022"},
        "timings_ms": {"asr": 620, "scoring": 480, "total": 1130},
    }


def test_to_server_shape_maps_fields_to_db_names():
    """The mapped dict must use dta-server's column names and surface labels."""

    result = to_server_shape(_recorded_payload())

    assert result["transcript"] == "no mä voisin antaa sulle vähän rahaa"
    assert result["scores"] == {
        "proficiency": 2.10,
        "fluency": 2.21,
        "pronunciation": 2.35,
        "range": 1.88,
        "accuracy": 1.79,
    }
    assert result["cefr_label"] == "A2"
    assert result["cefr_label_fine"] == "A2"
    assert result["clipped"] is False
    assert result["reportable_range"] == [1.14, 3.5]
    assert result["proficiency_uncalibrated"] == 1.98
    assert result["model_checkpoint"].endswith("s2022")


def test_to_server_shape_passes_clipped_through():
    """A clipped (boundary) score must surface clipped=True to the caller."""

    payload = _recorded_payload()
    payload["cefr"]["score"] = 3.5
    payload["cefr"]["clipped_to_calibration_range"] = True

    result = to_server_shape(payload)

    assert result["scores"]["proficiency"] == 3.5
    assert result["clipped"] is True


def test_asa_error_carries_status_and_detail():
    """ASAError exposes the HTTP status (None when unreachable) and detail."""

    unreachable = ASAError("inference service unreachable at http://inference:8000")
    assert unreachable.status is None

    failed = ASAError("scoring failed (404): unknown task", 404, "unknown task")
    assert failed.status == 404
    assert failed.detail == "unknown task"


def test_to_server_shape_requires_complete_payload():
    """A truncated payload (missing keys) raises rather than mapping silently."""

    with pytest.raises(KeyError):
        to_server_shape({"transcript": "x"})
