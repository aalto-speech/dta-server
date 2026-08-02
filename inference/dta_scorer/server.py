"""Thin HTTP wrapper — the interface of the INFERENCE container.

    uvicorn dta_scorer.server:app --host 0.0.0.0 --port 8000

This exists so the model can live in its own container with its own frozen environment
(torch/transformers/peft are version-brittle here) while your application container talks to
it over localhost. It is deliberately minimal: no auth, no database, no business logic, no
request queue. Those belong in your app tier. If this does not fit your stack, delete it and
call `ScoringPipeline` directly — the library is the real deliverable.

Concurrency: scoring is serialised inside ScoringPipeline, so this process handles one
request at a time regardless of worker threads. That is correct — the forward pass is
GPU-bound and holds ~10 GB of VRAM. Run one container per GPU and put a load balancer in
front if you need more throughput.

Expect ~2 s per request (ASR dominates; p90 ~4 s, worst seen 7.5 s on 2-minute audio), so
set client timeouts well above that — 30 s is a safe default.
"""
import logging
import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .config import DEVICE, MAX_UPLOAD_BYTES, check_weights, load_model_card
from .pipeline import ScoringPipeline
from .tasks import UnknownTask

log = logging.getLogger("dta_scorer")
# uvicorn configures handlers for its OWN loggers only and leaves the root logger bare, so
# without this the startup lines below never appear and a ~10 GB model load looks like a hang.
logging.basicConfig(
    level=os.environ.get("DTA_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = check_weights()
    if problems:
        # Fail at startup, loudly. A container that boots and then 500s on every request is
        # harder to diagnose than one that refuses to start.
        raise RuntimeError("cannot start:\n  " + "\n  ".join(problems))
    log.info("loading model on %s (~10 GB, expect ~20 s) ...", DEVICE)
    pipe = ScoringPipeline(device=DEVICE)
    if os.environ.get("DTA_WARMUP", "1") == "1":
        pipe.warmup()
    _state["pipeline"] = pipe
    log.info("ready: %s", pipe.card["checkpoint"])
    yield
    _state.clear()


app = FastAPI(title="DTA Finnish speaking assessment", version="1.0", lifespan=lifespan)


def _require() -> ScoringPipeline:
    pipe = _state.get("pipeline")
    if pipe is None:
        raise HTTPException(503, "model still loading")
    return pipe


@app.get("/health")
def health():
    """Readiness probe. 503 until the model is resident, so orchestrators wait it out."""
    pipe = _state.get("pipeline")
    if pipe is None:
        return JSONResponse({"status": "loading"}, status_code=503)
    return {
        "status": "ok",
        "device": pipe.device,
        "checkpoint": pipe.card["checkpoint"],
        "n_tasks": len(pipe.tasks),
        "calibration": pipe.calibrator.meta.get("method"),
        "reportable_cefr_range": [round(v, 2) for v in pipe.calibrator.output_range],
    }


@app.get("/tasks")
def tasks(corpus: str | None = None):
    """Task catalogue. `?corpus=dta` for the six DTA tasks the app will actually use."""
    pipe = _require()
    items = pipe.tasks.dta_tasks() if corpus == "dta" else pipe.tasks.all_tasks()
    return {"n": len(items), "tasks": [
        {"task_id": t.task_id, "task_name": t.task_name, "corpus": t.corpus,
         "prompt_fi": t.prompt_fi} for t in items]}


@app.get("/model_card")
def model_card():
    """Provenance, measured metrics and the known limits. Read this before showing a score."""
    return load_model_card()


@app.post("/score")
async def score(audio: UploadFile = File(...), task_id: str = Form(...),
                transcript: str | None = Form(None)):
    pipe = _require()
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"audio exceeds {MAX_UPLOAD_BYTES} bytes")

    suffix = os.path.splitext(audio.filename or "")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        return pipe.score_file(tmp.name, task_id, transcript=transcript or None)
    except UnknownTask as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        # libsndfile raises RuntimeError on formats it cannot open (browser MediaRecorder
        # webm/opus, m4a). That is a client-side encoding problem, not a server fault — your
        # app tier should transcode to 16 kHz mono wav before calling here.
        log.exception("could not decode audio")
        raise HTTPException(415, f"could not decode audio: {e}")
    finally:
        os.unlink(tmp.name)
