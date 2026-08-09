"""End-to-end inference: audio + task id -> transcript + calibrated CEFR + 4 dimensions.

This is a LIBRARY. Load it once at your app's startup and call it per request:

    from dta_scorer import ScoringPipeline
    pipe = ScoringPipeline()          # ~20 s on GPU, holds ~10 GB of VRAM
    result = pipe.score_file("answer.wav", "03_m")
    result = pipe.score_wave(wave_16k_mono_float32, "03_m")   # in-memory

Order of operations is fixed by how the model was trained:
    wave -> ASR transcript -> prompt(task prompt + transcript) -> scorer(mel frames, prompt)
The scorer READS the ASR text, so the transcript is an input to scoring, not a by-product.

Thread safety: scoring is serialised on an internal lock. The forward pass is GPU-bound, so
concurrent calls would trade throughput for OOM risk without going faster. Scale with more
processes on more GPUs, not more threads.
"""
import logging
import threading
import time

import numpy as np

from .asr import FinnishASR
from .audio import chunk_to_features, load_wave
from .calibration import IsotonicCalibrator
from .config import DEVICE, DIM_ORDER, SAMPLE_RATE, load_model_card
from .prompt import build_llm_input, build_prompt
from .relevance import ENABLED as RELEVANCE_ENABLED
from .relevance import RelevanceJudge
from .scorer import MCasaScorer
from .tasks import TaskCatalogue

log = logging.getLogger(__name__)

CEFR_LABELS = {0: "<A1", 1: "A1", 2: "A2", 3: "B1", 4: "B2", 5: "C1", 6: "C2"}


def coarse_label(score: float) -> str:
    """The project's reporting convention: FLOOR to the coarse band, so 2.9 is still A2."""
    band = int(np.clip(np.floor(float(score) + 1e-8), 0, 6))
    return CEFR_LABELS[band]


def fine_label(score: float) -> str:
    """Half-step label with plus-levels (2.5 -> 'A2+'), the scale the DTA raters used.

    Band 0 is the open-ended "below A1" bucket and takes no plus-level, so anything under
    1.0 is '<A1' rather than '<A1+'.
    """
    half = round(float(np.clip(score, 0, 6)) * 2) / 2
    if half < 1.0:
        return CEFR_LABELS[0]
    base = CEFR_LABELS[int(np.floor(half + 1e-8))]
    is_plus = abs(half - np.floor(half) - 0.5) < 1e-8
    return f"{base}+" if is_plus else base


class ScoringPipeline:
    def __init__(self, device: str = DEVICE):
        self.device = device
        self.tasks = TaskCatalogue.load()
        self.calibrator = IsotonicCalibrator.load()
        self.card = load_model_card()
        self.asr = FinnishASR(device=device)
        self.scorer = MCasaScorer(device=device)
        # Reuses the scorer's Qwen with the adapter switched off — no second model, no extra
        # VRAM. Set DTA_RELEVANCE_CHECK=0 to serve scores without the content channel.
        self.judge = RelevanceJudge(self.scorer, device=device) if RELEVANCE_ENABLED else None
        self._lock = threading.Lock()

    def warmup(self) -> None:
        """Force CUDA kernel/graph init so the first real request is not seconds slower."""
        self.score_wave(np.zeros(SAMPLE_RATE, dtype=np.float32),
                        self.tasks.dta_tasks()[0].task_id, transcript="warmup")

    def _forward(self, wave: np.ndarray, task, transcript: str) -> dict:
        feats, bounds, info = chunk_to_features(wave, self.asr.feature_extractor)
        prompt = build_prompt(build_llm_input(
            task_id=task.task_id, task_name=task.task_name,
            question=task.prompt_fi, answer=transcript))
        out = self.scorer.score(feats, bounds, task.model_task_id, prompt)
        out["audio"] = info
        return out

    def _judge(self, task, transcript: str) -> dict | None:
        """Topical-relevance verdict, or None when the check is off or has failed.

        FAIL OPEN, deliberately. The score above this line was computed correctly; a broken
        side channel must not cost the learner that result. Callers render a missing block
        as "not checked" — see docs/FRONTEND.md.
        """
        if self.judge is None:
            return None
        try:
            return self.judge.judge(task, transcript)
        except Exception:  # pylint: disable=broad-exception-caught  # never fail the score
            log.exception("relevance check failed; returning the score without it")
            return None

    def score_file(self, audio_path: str, task_key: str,
                   transcript: str | None = None) -> dict:
        """Score an audio FILE (wav/flac/ogg — anything libsndfile reads).

        transcript: pass a text to bypass ASR. Evaluation and debugging only — the model was
        trained on ASR output and behaves differently on verbatim human transcripts.
        """
        return self.score_wave(load_wave(audio_path), task_key, transcript=transcript)

    def score_wave(self, wave, task_key: str, transcript: str | None = None) -> dict:
        """Score an IN-MEMORY waveform: mono float32 at 16 kHz.

        This is the entry point for a server that already holds decoded audio. If your audio
        is at another sample rate, resample with scipy.signal.resample_poly (NOT librosa or
        torchaudio) — that is what produced the training features, and resamplers differ
        enough to move the mel spectrogram.
        """
        t0 = time.perf_counter()
        task = self.tasks.get(task_key)

        wave = np.ascontiguousarray(np.asarray(wave, dtype=np.float32).squeeze())
        if wave.ndim != 1:
            raise ValueError(f"expected mono audio, got shape {wave.shape}")
        t_load = time.perf_counter()

        used_asr = transcript is None
        with self._lock:
            if used_asr:
                transcript = self.asr.transcribe(wave)
            t_asr = time.perf_counter()
            raw = self._forward(wave, task, transcript)
            t_score = time.perf_counter()
            content = self._judge(task, transcript)
        t_judge = time.perf_counter()

        cefr_raw = raw["cefr_raw"]
        cefr_cal = self.calibrator(cefr_raw)
        lo, hi = self.calibrator.output_range

        return {
            "task": {"task_id": task.task_id, "task_name": task.task_name,
                     "corpus": task.corpus, "model_task_id": task.model_task_id},
            "transcript": transcript,
            "transcript_source": "asr:finnish_v3_whisper_medium" if used_asr else "supplied",
            "cefr": {
                "score": round(cefr_cal, 3),
                "label": coarse_label(cefr_cal),
                "label_fine": fine_label(cefr_cal),
                "calibration": "isotonic",
                "score_uncalibrated": round(cefr_raw, 3),
                "clipped_to_calibration_range": self.calibrator.clipped(cefr_raw),
                "reportable_range": [round(lo, 2), round(hi, 2)],
            },
            "dimensions": {
                d: {"score": round(raw["dims_raw"][d], 3),
                    "label": coarse_label(raw["dims_raw"][d]),
                    "label_fine": fine_label(raw["dims_raw"][d]),
                    "calibration": None}
                for d in DIM_ORDER
            },
            # Topical relevance: does the transcript answer THIS task? A side channel — it
            # is computed after the scores and cannot change them. None = not checked
            # (disabled, or the judge failed), which callers must read as "show the score".
            "content": content,
            # Stated, not hidden: the headline CEFR is calibrated and the dims are not, so the
            # OLS of the shown dims does not equal the shown CEFR (mean gap 0.217 on test).
            # Per-dim calibration was measured and rejected — it worsens 3 of the 4 dims.
            "consistency_note":
                "cefr.score is isotonic-calibrated; dimension scores are raw model outputs. "
                "They are on the same 0-6 CEFR scale but are not algebraically consistent.",
            "audio": raw["audio"],
            "model": {
                "checkpoint": self.card["checkpoint"],
                "test_rmse_calibrated": self.card["test_metrics_calibrated"][
                    "test_rmse_overall_isocal"],
            },
            "timings_ms": {
                "audio_load": round((t_load - t0) * 1000, 1),
                "asr": round((t_asr - t_load) * 1000, 1),
                "scoring": round((t_score - t_asr) * 1000, 1),
                "relevance": round((t_judge - t_score) * 1000, 1),
                "total": round((t_judge - t0) * 1000, 1),
            },
        }
