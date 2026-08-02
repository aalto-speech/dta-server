"""Waveform loading and 30 s mel chunking — reproduces data_loaders/sandi_dataset.py.

Two details here are load-bearing and easy to get wrong:

* Resampling uses scipy.signal.resample_poly, not librosa/torchaudio. This is what training
  used, and different resamplers give measurably different mel features.
* Audio is truncated to MAX_CHUNKS x 30 s = 120 s. Anything past that is silently dropped by
  the encoder, so the caller is told about it (`truncated` in the returned info) rather than
  finding out from a bad score.
"""
import math
from math import gcd

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly

from .config import CHUNK_DURATION_SEC, MAX_CHUNKS, SAMPLE_RATE


def load_wave(path: str, target_sr: int = SAMPLE_RATE) -> np.ndarray:
    """Mono float32 at target_sr. soundfile only (librosa/torchaudio are ABI-broken in the
    research env and were never used to produce the training features)."""
    wave, sr = sf.read(path, dtype="float32")
    if wave.ndim > 1:
        wave = wave.mean(axis=1)
    if sr != target_sr:
        g = gcd(int(sr), int(target_sr))
        wave = resample_poly(wave, target_sr // g, sr // g).astype(np.float32)
    return np.ascontiguousarray(wave, dtype=np.float32)


def chunk_to_features(wave: np.ndarray, feature_extractor) -> tuple[torch.Tensor, torch.Tensor, dict]:
    """wave -> (input_features (C,80,3000), clip_bounds (1,), info).

    A production request is always ONE clip, so clip_bounds is a single cumulative end-time
    and the encoder's per-frame answer embedding is constant 0 — exactly the DTA monologue
    case it was trained on.
    """
    chunk_samples = CHUNK_DURATION_SEC * SAMPLE_RATE
    full = wave if wave.size else np.zeros(chunk_samples, dtype=np.float32)

    n_needed = max(1, int(math.ceil(full.size / chunk_samples)))
    n = min(n_needed, MAX_CHUNKS)
    chunks = []
    for j in range(n):
        chunk = full[j * chunk_samples:(j + 1) * chunk_samples]
        if len(chunk) < chunk_samples:
            chunk = np.pad(chunk, (0, chunk_samples - len(chunk)))
        chunks.append(chunk)

    input_features = feature_extractor(
        chunks, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_features

    # clip_bounds is the cumulative end-time of each clip, computed on the FULL waveform
    # before truncation — matching _load_chunks, which derives bounds from clip lengths.
    clip_bounds = torch.tensor([full.size / SAMPLE_RATE], dtype=torch.float32)
    info = {
        "duration_sec": round(full.size / SAMPLE_RATE, 3),
        "n_chunks": n,
        "truncated": n_needed > MAX_CHUNKS,
        "max_scored_sec": MAX_CHUNKS * CHUNK_DURATION_SEC,
    }
    return input_features, clip_bounds, info
