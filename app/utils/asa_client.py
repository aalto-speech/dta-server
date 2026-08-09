"""HTTP client for the inference container — COPY THIS FILE INTO dta-server.

Standalone by design: it imports only httpx (already in dta-server's conda-lock) and the
standard library, so it can live at `app/utils/asa_client.py` without vendoring anything else.
Nothing else in this package needs to exist on the application side.

    from app.utils.asa_client import ASAClient, ASAError

    client = ASAClient(base_url=os.getenv("ASA_URL", "http://inference:8000"))
    result = await client.assess(content, task_id=data.task_id, filename=file.filename)

    result["scores"]      -> {proficiency, fluency, pronunciation, range, accuracy}
    result["transcript"]  -> str, from finnish-v3 Whisper

The returned `scores` keys are dta-server's column names, so they drop straight into
AssessmentCreateInput. See INTEGRATION.md for the full patch.

TWO THINGS THE CALLER MUST HANDLE, both of which are silent if ignored:

1. SCALE. These are CEFR values on a 0-6 scale (0=<A1, 1=A1, 2=A2, 3=B1, ...), not a rating
   out of 5. dta-server's `Score` bound and DB CHECKs were widened to 0-6 accordingly --
   but rendering "2.1/5" to a learner for what is actually A2 is wrong. Use `cefr_label` /
   `cefr_label_fine` in the UI.

2. ONLY `proficiency` IS CALIBRATED. The four dimensions are raw model outputs. They share
   the CEFR scale but are not algebraically consistent with the holistic score (mean gap 0.22
   on the held-out test set). Per-dimension calibration was measured and rejected because it
   made 3 of the 4 dimensions worse.
"""
from __future__ import annotations

import httpx

# Scoring is ~2 s median, p90 ~4 s, worst observed 7.5 s on 2-minute audio, and requests are
# serialised on the GPU -- so a queued request waits behind the one in flight. 60 s leaves
# room for a couple of queued requests before the caller gives up.
DEFAULT_TIMEOUT = 60.0


class ASAError(RuntimeError):
    """Inference service failed. `.status` is the HTTP code, or None if unreachable.

    `.timed_out` separates "the request ran out of time" (scorer alive but busy --
    worth retrying later) from "the connection failed" (scorer down -- retrying is
    pointless). Both arrive with status=None, so the flag is the only way to tell.
    """

    def __init__(self, message: str, status: int | None = None, detail: str | None = None,
                 timed_out: bool = False):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.timed_out = timed_out


class ASAClient:
    """Async client for the inference container's /score and /health endpoints."""

    def __init__(self, base_url: str = "http://inference:8000",
                 timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def health(self) -> dict:
        """Readiness. Returns 503 while the model loads (~20 s), so treat that as 'not yet'."""
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{self.base_url}/health")
            if r.status_code != 200:
                raise ASAError(f"inference not ready ({r.status_code})", r.status_code)
            return r.json()

    async def assess(self, audio: bytes, task_id, filename: str = "audio.wav",
                     transcript: str | None = None) -> dict:
        """Score one recording.

        audio:     WAV bytes (16 kHz mono is ideal; anything libsndfile reads works)
        task_id:   dta-server's integer task id, resolved via assets/task_id_map.json on the
                   service side. An unmapped id returns 404 rather than scoring the wrong
                   task -- do not "fix" that by guessing an id.
        transcript: supply to bypass ASR. Debugging only; the model was trained on ASR text
                   and behaves differently on verbatim human transcripts.
        """
        files = {"audio": (filename or "audio.wav", audio, "audio/wav")}
        data = {"task_id": str(task_id)}
        if transcript is not None:
            data["transcript"] = transcript

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}/score", files=files, data=data)
        except httpx.TimeoutException as e:
            raise ASAError(
                f"inference service timed out after {self.timeout}s (busy or wedged): {e}",
                timed_out=True) from e
        except httpx.RequestError as e:
            raise ASAError(f"inference service unreachable at {self.base_url}: {e}") from e

        if r.status_code != 200:
            detail = _detail(r)
            raise ASAError(f"scoring failed ({r.status_code}): {detail}",
                           r.status_code, detail)

        return to_server_shape(r.json())


def to_server_shape(payload: dict) -> dict:
    """Inference response -> dta-server's field names.

    Kept as a free function so it can be unit-tested against a recorded payload without a
    running service.
    """
    dims = payload["dimensions"]
    cefr = payload["cefr"]
    return {
        "transcript": payload["transcript"],
        "scores": {
            # holistic CEFR, isotonic-calibrated -- the one number to show
            "proficiency": cefr["score"],
            # raw model outputs, same 0-6 CEFR scale, NOT calibrated
            "fluency": dims["fluency"]["score"],
            "pronunciation": dims["pronunciation"]["score"],
            "range": dims["range"]["score"],
            "accuracy": dims["accuracy"]["score"],
        },
        "cefr_label": cefr["label"],              # coarse, floored: 2.9 -> "A2"
        "cefr_label_fine": cefr["label_fine"],    # half-steps: 2.5 -> "A2+"
        # Same banding applied to each raw dimension, so a client showing five labelled
        # rows derives none of them locally. NB fine labels ROUND to the nearest half
        # step (2.3 -> "A2+"); they are not floor-based intervals.
        "dimension_labels": {
            d: {"label": dims[d]["label"], "label_fine": dims[d]["label_fine"]}
            for d in ("fluency", "pronunciation", "range", "accuracy")
        },
        # True when the raw prediction fell outside the calibrator's fitted range, so the
        # score is a boundary value rather than a measurement. The model cannot resolve
        # above B1+ (3.5) at all -- surface this rather than presenting a capped score as real.
        "clipped": cefr["clipped_to_calibration_range"],
        "reportable_range": cefr["reportable_range"],
        "proficiency_uncalibrated": cefr["score_uncalibrated"],
        "task": payload["task"],
        "audio": payload["audio"],               # duration_sec, truncated (>120 s is cut)
        "model_checkpoint": payload["model"]["checkpoint"],
    }


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail", r.text))[:500]
    except Exception:  # pylint: disable=broad-exception-caught  # any non-JSON body
        return r.text[:500]
