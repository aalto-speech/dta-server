# Patching dta-server to use the real model

Written against `aalto-speech/dta-server` @ main. Today `assess_speech_request` transcribes
with `whisper.load_model("small")` and returns `round(uniform(0, 5), 1)` for all five scores.
This replaces both halves: real ASR (finnish-v3 Whisper medium) and real scores, from a
separate GPU container.

Nothing here modifies dta-server automatically — apply it yourself, in this order.

---

## 0. What moves where

| | before | after |
|---|---|---|
| ASR | `whisper-small`, inside the `dta` container | finnish-v3 medium, inference container |
| scores | `random.uniform(0, 5)` | M-CASA, isotonic-calibrated CEFR |
| GPU | none | inference container only |
| `dta` conda lock | contains `pytorch`, `openai-whisper`, `torchaudio`, `transformers` | can drop all four |

Deployment goes from 2 containers (`dta`, `caddy`) to 3 (`dta`, `caddy`, `inference`).
SQLite stays where it is — it was never the reason to split.

## 1. Deploy the inference container

```bash
./inference/deploy/fetch_weights.sh        # pulls ~15.9 GB from Usin2705/dta_asa
systemctl --user restart dta-compose.service
# the service publishes no host port on purpose — check health from inside:
podman exec dta-inference curl -fsS http://localhost:8000/health   # 503 until the model is resident (~20 s on GPU)
```

Verify before wiring anything up:

```bash
podman exec dta-inference python selftest.py
```

Details, including revision pinning and the image/weights hash guard: `docs/WEIGHTS.md`.

## 2. Copy the client

`inference/dta_scorer/client.py` → `app/utils/asa_client.py`

It imports only `httpx` (already in your `conda-lock.yaml`) and the standard library. No
other file from this package is needed on the application side.

Add to `app/config.py`:

```python
asa_url: str = os.getenv("ASA_URL", "http://inference:8000")
```

## 3. Replace the scoring body

In `app/services/speech_assessment_service.py`, delete `_transcribe()` and the five
`round(uniform(0, 5), 1)` lines, then:

```python
from app.utils.asa_client import ASAClient, ASAError

_asa = ASAClient(base_url=SETTINGS.asa_url)

# ... inside assess_speech_request, after the audio validators and the file write:
try:
    result = await _asa.assess(content, task_id=data.task_id,
                               filename=data.file.filename or "audio.wav")
except ASAError as e:
    logger.error("ASA scoring failed for user %s: %s", data.guid, e)
    raise HTTPException(status_code=503, detail="scoring temporarily unavailable") from e

transcript = result["transcript"]
scores = result["scores"]          # proficiency, fluency, pronunciation, range, accuracy
```

Then pass `**scores` into `AssessmentCreateInput` (note its field is `range_score`, while the
client returns `range` — rename at the call site).

`app/utils/whisper_model.py` can now be deleted, along with `pytorch`, `openai-whisper`,
`torchaudio` and `transformers` from `environment.yaml`. Re-lock afterwards.

## 4. Score scale: CEFR 0–6 vs dta-server's 0–5 — note, not a blocker

Assessed and deliberately deprioritised: 5 is C1, and the calibrated score is hard-capped at
3.50 (B1+), so the bound can never be reached. Make the two free edits below when convenient;
do not migrate existing databases for it.

The model emits CEFR values on a 0–6 scale (0 = `<A1`, 1 = `A1`, 2 = `A2`, 3 = `B1`, 4 = `B2`,
5 = `C1`, 6 = `C2`). Your current bound is 0–5:

```python
# app/models/speech_assessment.py
Score = Annotated[float, Field(ge=0, le=5)]     # -> ge=0, le=6
```
```sql
-- app/schema.sql, all five columns
accuracy REAL CHECK (accuracy IS NULL OR (accuracy BETWEEN 0 AND 5))   -- -> 0 AND 6
```

Today's values all fit under 5 (calibrated CEFR is capped at 3.50), so **nothing will raise
and no data will be rejected** — which is exactly why it is easy to miss. The bound is wrong
in principle, and a future model without the 3.5 ceiling would start silently failing
validation.

Existing rows hold random 0–5 values from the placeholder implementation. Decide explicitly
whether to delete or flag them; they are not comparable to real scores.

## 5. Map your integer task ids — DONE

`assessments.task_id` is an INTEGER; this package uses strings (`"03_m"`). The map in
`assets/task_id_map.json` was confirmed against the app on 2026-08-01:

| your task_id | package task_id | task |
|---|---|---|
| 1 | `03_m` | friend asks to borrow 100 euros (30 s reaction) |
| 2 | `03_n` | voice message explaining absence (30 s reaction) |
| 3 | `04_h` | what you normally do at home (1 min monologue) |
| 4 | `04_i` | which languages you use and where (1 min monologue) |
| 5 | `04_test` | describe the shopping picture (dta-task5) |

`04_dev` (dta-task3) is unused by the app: a development-phase version of the same picture
task. It stays reachable by string id, but has no integer mapping, so it cannot be hit by
accident. Note that `04_dev` and `04_test` are **both** picture tasks on the same image — the
prompt text cannot tell them apart, only the `_dev`/`_test` naming can.

Why this mattered more than a normal config check: the model conditions on a learned task
embedding indexed by position, so a wrong-but-valid id produces a plausible score with no
error anywhere. The initial guess (ascending by DTA task naming) had three of the five wrong,
and a first correction still had 5 wrong by reading the prompt text instead of the naming.
Unmapped integers raise `UnknownTask` → HTTP 404 by design; if the app's task list changes,
fix the map, never the caller.

One inconsistency to clean up while you are here: `scripts/seed_db.py:261` writes
`f"task-{rng.randint(1, 40)}"` — a string, and out of range — into that INTEGER column.

## 6. Use the labels in the UI

`proficiency` is the holistic CEFR score and the only calibrated number. The client also
returns:

- `cefr_label` — coarse, floored (`2.9` → `"A2"`), matching the project's reporting convention
- `cefr_label_fine` — half-steps with plus-levels (`2.5` → `"A2+"`), the scale the DTA raters used

Show a label. A CEFR value rendered as a bare number invites reading it as a mark out of 5 or 6.

**The four dimensions are raw, uncalibrated model outputs.** They are on the same CEFR scale
but are not algebraically consistent with `proficiency` (mean gap 0.22 on the held-out test
set). If a learner can see all five at once, expect "why is my overall 2.1 when my parts
average 2.3?" — the honest answer is that the overall score is corrected for a known
compression and the parts are not. Per-dimension calibration was measured and rejected: it
makes 3 of the 4 dimensions worse.

## 7. Handle `clipped`

The calibrator cannot produce a value outside `[1.14, 3.50]`. When `clipped` is true the raw
prediction fell outside the fitted range and the score is a boundary, not a measurement.

**The model cannot certify B2 or above.** A genuinely B2 speaker returns 3.50, identical to a
B1+ speaker. Decide what the app does with that — suppress the number, show "B1+ or above", or
flag for human review — but do not present it as a measured result.

## 8. Operational notes

- **Latency**: median 2.1 s, p90 4.2 s, max 7.5 s on 2-minute audio. ASR dominates; scoring
  is only 225 ms. Client timeout defaults to 60 s.
- **Throughput**: ~0.5 req/s per GPU. Scoring is serialised inside the pipeline, so extra
  uvicorn workers only double VRAM. Scale with containers on more GPUs.
- **Audio ≥ 120 s is truncated** (4 × 30 s chunks) and the response sets `audio.truncated`.
  Your `validate_audio_duration` should reject or warn before that point.
- **Format**: libsndfile reads wav/flac/ogg, not browser `webm/opus` or `m4a`. You already
  validate WAV headers, so this is consistent — keep transcoding at the app tier.
- **Restart policy**: the model reloads in ~20 s on GPU (~10 min on CPU staging). `/health`
  returns 503 until resident; the image healthcheck has a 900 s start period to cover the
  CPU case.

## 9. What to check after wiring it up

Score a known recording end to end and compare against `inference/README.md` §6. On the
98 held-out test recordings the served model reaches calibrated RMSE **0.3844**, against the
research checkpoint's 0.3854 — so if a familiar recording comes back wildly different, the
task id mapping is the first thing to suspect, not the model.
