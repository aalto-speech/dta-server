"""Paths and frozen constants. Every value here is fixed by the trained checkpoint.

Nothing in this file is a tuning knob. Changing max_chunks, frame_pool, the rubric text or
the prompt template changes what the model is fed relative to what it was trained on, and
the scores stop meaning what the model card says they mean.
"""
import json
import os
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent

# --- where the weights live ------------------------------------------------------------
# Override with DTA_WEIGHTS_DIR if the server keeps large files elsewhere (a model volume,
# a shared NFS mount). Layout is created by scripts/dta_production/stage_weights.py.
WEIGHTS_DIR = Path(os.environ.get("DTA_WEIGHTS_DIR", PKG_ROOT / "weights"))
SCORER_WEIGHTS = WEIGHTS_DIR / "scorer" / "model.safetensors"
WHISPER_DIR = WEIGHTS_DIR / "whisper_finnish_v3"    # ASR *and* acoustic-encoder backbone
QWEN_DIR = WEIGHTS_DIR / "qwen_base"

ASSETS_DIR = Path(os.environ.get("DTA_ASSETS_DIR", PKG_ROOT / "assets"))
TASKS_JSON = ASSETS_DIR / "tasks.json"
CALIBRATION_JSON = ASSETS_DIR / "calibration.json"
# Optional: application integer task_id -> this package's string task ids.
TASK_ID_MAP_JSON = ASSETS_DIR / "task_id_map.json"
MODEL_CARD_JSON = ASSETS_DIR / "model_card.json"

# --- runtime ---------------------------------------------------------------------------
DEVICE = os.environ.get("DTA_DEVICE", "cuda")
# Training and the reported evaluation both ran under bf16 autocast. Keeping it is a parity
# requirement, not an optimisation: fp32 inference gives slightly different scores than the
# ones in the model card.
AUTOCAST_DTYPE = os.environ.get("DTA_AUTOCAST_DTYPE", "bfloat16")
ASR_BATCH_SIZE = int(os.environ.get("DTA_ASR_BATCH_SIZE", "1"))
MAX_UPLOAD_BYTES = int(os.environ.get("DTA_MAX_UPLOAD_BYTES", str(64 * 1024 * 1024)))

# --- frozen model geometry (from assets/model_card.json; duplicated as literals so a
#     missing asset file fails loudly rather than silently changing the model) -----------
SAMPLE_RATE = 16000
CHUNK_DURATION_SEC = 30
MAX_CHUNKS = 4                     # 4 x 30 s = 120 s of audio; the rest is discarded
MAX_TEXT_LEN = 1024                # Qwen prompt truncation length
FRAME_POOL = 2
N_SOFT_TOKENS = 4
N_TASKS = 64
AGG_LAYERS = 2
WHISPER_LORA_R = 16
QWEN_LORA_R = 64
QWEN_LORA_ALPHA = 128
ACOUSTIC_PROJECTOR_DROPOUT = 0.1   # inert in eval(), kept so the state dict shape matches
ATTN_IMPL = "sdpa"
TARGET_LANGUAGE = "Finnish"
PROMPT_VERSION = "finnish-asr-content-v1-finnishv3"
SCORE_CUE = "\n\n<SCORE>:"

DIM_ORDER = ["fluency", "pronunciation", "range", "accuracy"]

# ASR decode settings, matching scripts/run_asr.py (the transcripts the scorer was trained on)
ASR_LANGUAGE = "fi"
ASR_TASK = "transcribe"
ASR_CHUNK_LENGTH_S = 30
ASR_STRIDE_LENGTH_S = 5


def load_model_card() -> dict:
    return json.loads(MODEL_CARD_JSON.read_text())


def check_weights() -> list[str]:
    """Return a list of human-readable problems; empty means ready to serve."""
    problems = []
    if not SCORER_WEIGHTS.is_file():
        problems.append(f"missing scorer weights: {SCORER_WEIGHTS}")
    for d, what in ((WHISPER_DIR, "Finnish Whisper (ASR + acoustic backbone)"),
                    (QWEN_DIR, "Qwen3.5-2B base")):
        if not (d / "config.json").is_file():
            problems.append(f"missing {what}: {d}/config.json")
    for f in (TASKS_JSON, CALIBRATION_JSON, MODEL_CARD_JSON):
        if not f.is_file():
            problems.append(f"missing asset: {f}")
    if not problems:
        problems.extend(check_weights_match_assets())
    return problems


def checkpoint_fingerprint(path=None) -> str:
    """sha256 of the checkpoint's first 64 MB. Matches the value model_card.json records.

    Partial rather than whole-file: 8.24 GB takes ~30 s to hash and this runs at every
    startup. The first 64 MB covers the safetensors header plus the first tensors, which is
    ample to distinguish two different training runs -- it is a wiring check, not a security
    control.
    """
    import hashlib
    h = hashlib.sha256()
    read = 0
    with open(path or SCORER_WEIGHTS, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            read += len(chunk)
            if read >= (64 << 20):
                break
    return h.hexdigest()


def check_weights_match_assets() -> list[str]:
    """Do the MOUNTED weights belong to the checkpoint this image's assets were built from?

    THE FAILURE THIS EXISTS FOR. The image (code + assets) and the weights volume are updated
    by separate steps: `podman pull` for one, a manual copy or HF download for the other. Do
    one without the other and the service starts happily with, say, a new calibrator or a
    renumbered task catalogue applied to an old checkpoint -- and scores every recording
    plausibly and wrongly, with nothing in any log.

    A hash comparison turns that into a refusal to boot. Set DTA_SKIP_WEIGHT_CHECK=1 to
    bypass (only meaningful while deliberately testing a new checkpoint against old assets).
    """
    if os.environ.get("DTA_SKIP_WEIGHT_CHECK") == "1":
        return []
    try:
        card = load_model_card()
        expected = card["weights"]["sha256_first_64mb"]
    except (OSError, ValueError, KeyError):
        return ["model_card.json has no weights.sha256_first_64mb — cannot verify that the "
                "mounted weights match this image's assets"]
    actual = checkpoint_fingerprint()
    if actual != expected:
        return [
            "WEIGHTS DO NOT MATCH THIS IMAGE.\n"
            f"      mounted  {SCORER_WEIGHTS}\n"
            f"        sha256(first 64MB) {actual[:32]}...\n"
            f"      expected by assets/model_card.json ({card.get('checkpoint','?')})\n"
            f"        sha256(first 64MB) {expected[:32]}...\n"
            "      The image and the weights volume are updated separately; one of them is "
            "stale.\n"
            "      Refresh the asa-weights volume, or pull the image matching these weights. "
            "See docs/WEIGHTS.md."]
    return []
