# DTA Finnish speaking assessment — deployable scorer

> ### ✓ Nothing blocking. Task-id map confirmed 2026-08-01.
> One standing note in **[ACTION_REQUIRED.md](ACTION_REQUIRED.md)**: scores are CEFR 0–6 while
> dta-server declares 0–5 — assessed as unreachable (5 is C1; the calibrated score is capped
> at 3.50) so no migration is needed. Wiring: **[INTEGRATION.md](INTEGRATION.md)**.
> Deploying: **[docs/DEPLOY_STAGING.md](docs/DEPLOY_STAGING.md)**.

Records → transcript → CEFR + 4 analytic dimensions, for the DTA speaking tasks.

Model: `finnish-v3_le40_mcasa_noac_eqcap2x_ttsfluall3_s2022` (M-CASA), with **isotonic
calibration applied to the CEFR score at inference**.

| | raw model | **served (calibrated)** |
|---|---|---|
| Test RMSE (98 held-out DTA recordings) | 0.4249 | **0.3854** |
| Prediction sd (true 0.686) | 0.393 | 0.662 |
| Spearman with rater CEFR | 0.855 | 0.855 (unchanged — calibration is monotone) |

---

## 1. What it does

```
audio (wav/flac) ─┬─► finnish-v3 Whisper ──► transcript ──┐
                  │                                        ├─► prompt ─► Qwen3.5-2B (LoRA)
                  └─► Whisper encoder frames ─► aggregator ┘        │
                                    │                                │
                        CLS_flu / CLS_pron heads          content head
                                    │                                │
                          fluency, pronunciation            range, accuracy
                                    └──────────┬───────────────────┘
                                        frozen OLS  →  CEFR  →  isotonic calibration
```

The transcript is an **input** to scoring, not a by-product: the content head reads it. The
same Whisper model does double duty — it transcribes, and its encoder frames feed the
acoustic branch.

## 2. Requirements

- 1 GPU, **≥ 16 GB VRAM**, 24 GB comfortable. Roughly 10 GB of weights stay resident:
  Qwen3.5-2B (bf16, 4 GB) + the scorer's Whisper encoder (fp32, 3 GB) + a *second* Whisper
  for ASR (fp16, 1.5 GB). The two Whispers are not redundant — the scorer's copy was further
  trained and is overwritten by the checkpoint, while ASR needs the original decoder.
- ~20 GB disk for weights
- CPU-only will run but takes tens of seconds per request; it is for smoke tests, not service
- `libsndfile` (`apt install libsndfile1`) — soundfile's C dependency

## 3. Install

```bash
# on the server
cd inference

# match torch to the server's driver FIRST, then the rest
pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

Conda alternative if pip's torch fights the system CUDA:

```bash
conda env create -f environment.yml && conda activate dta-scorer
```

> The pins are the versions the checkpoint was trained under. `transformers` and `peft` are
> the two that genuinely matter: both have changed state-dict key layouts across minor
> versions, and the loader is strict — a mismatch fails at startup rather than quietly
> serving a partly-random head. If you must move off a pin, run
> `scripts/dta_production/verify_parity.py --mode scored` afterwards and check the
> deviation is still ~1e-3.

## 4. Weights

Weights are **not** in this directory or in the container image (~15.9 GB that change only on
retrain). They live in the `asa-weights` podman volume, pulled from
**[Usin2705/dta_asa](https://huggingface.co/Usin2705/dta_asa)** (public, no credentials):

```bash
./deploy/fetch_weights.sh                 # ~15.9 GB, resumable
systemctl --user restart dta-compose.service
```

```
weights/scorer/model.safetensors    8.2 GB   trained M-CASA checkpoint (complete state dict)
weights/whisper_finnish_v3/         3.1 GB   ASR model + acoustic-encoder backbone
weights/qwen_base/                  4.6 GB   Qwen3.5-2B base
```

The container **verifies at startup** that these weights match the image's `assets/` (sha256
of the checkpoint's first 64 MB) and refuses to serve if they do not — the image and the
weights volume are updated by separate commands, and a mismatch would otherwise score
plausibly and wrongly. Pin a revision in production rather than tracking `main`. Full detail:
[`docs/WEIGHTS.md`](docs/WEIGHTS.md).

Set `DTA_WEIGHTS_DIR` if the weights live somewhere other than `./weights`.

## 5. Run

```bash
# one-off check
python score_cli.py --audio answer.wav --task_id 03_m

# service
uvicorn dta_scorer.server:app --host 0.0.0.0 --port 8000
```

### API

| | |
|---|---|
| `GET /health` | readiness, loaded checkpoint, reportable CEFR range |
| `GET /tasks` | task catalogue (`?corpus=dta` for the 6 DTA tasks) |
| `GET /model_card` | full provenance, metrics and limits |
| `POST /score` | `multipart: audio=<file>, task_id=<str>[, transcript=<str>]` |

```bash
curl -F audio=@answer.wav -F task_id=03_m http://localhost:8000/score
```

```jsonc
{
  "task": {"task_id": "03_m", "task_name": "dta-task2_a", "model_task_id": 23},
  "transcript": "no mä voisin antaa sulle vähän rahaa mutta ...",
  "transcript_source": "asr:finnish_v3_whisper_medium",
  "cefr": {
    "score": 2.10,               // ← calibrated; this is the number to show
    "label": "A2", "label_fine": "A2",
    "calibration": "isotonic",
    "score_uncalibrated": 1.98,
    "clipped_to_calibration_range": false,
    "reportable_range": [1.14, 3.5]
  },
  "dimensions": {                // raw model outputs — see §7
    "fluency":       {"score": 2.21, "label": "A2", "label_fine": "A2"},
    "pronunciation": {"score": 2.35, "label": "A2", "label_fine": "A2+"},
    "range":         {"score": 1.88, "label": "A1", "label_fine": "A2"},
    "accuracy":      {"score": 1.79, "label": "A1", "label_fine": "A2"}
  },
  "audio": {"duration_sec": 27.4, "n_chunks": 1, "truncated": false, "max_scored_sec": 120},
  "timings_ms": {"asr": 620, "scoring": 480, "total": 1130}
}
```

Scoring is serialised behind a lock — the forward pass is GPU-bound, so concurrent requests
would trade throughput for OOM risk. **Scale with replicas, not threads.**

### Task ids

`GET /tasks?corpus=dta` lists the six DTA tasks. Accepts the package task_id, the task_name,
or the app's integer id (via `assets/task_id_map.json`):

| app id | task_id | task_name | |
|---|---|---|---|
| 1 | `03_m` | `dta-task2_a` | reaction: friend asks to borrow 100 euros |
| 2 | `03_n` | `dta-task2_b` | reaction: voice message explaining absence |
| 3 | `04_h` | `dta-task4_a` | monologue: what you do at home |
| 4 | `04_i` | `dta-task4_b` | monologue: which languages you use and where |
| 5 | `04_test` | `dta-task5` | picture: shopping habits |
| — | `04_dev` | `dta-task3` | development-phase version of the picture task; **not used** |

`04_dev` and `04_test` are the *same picture* — the suffix marks development vs the real DTA
test, and the prompt text cannot tell them apart (only `04_dev`'s wording happens to say
"Kerro kuvasta"). Only `04_test` is served; `04_dev` has no integer mapping so it cannot be
reached by accident.

The catalogue also carries the 29 DigiTala tasks the model trained on. `model_task_id` is a
row index into a learned embedding table and is **frozen** in `assets/tasks.json` — in the
research repo it is recomputed by scanning every split, so adding one task renumbers all of
them. Never regenerate it against a changed corpus without redeploying the model.

## 6. Verify the deployment

**On the server** — no research data needed:

```bash
python selftest.py
```

Loads the model, checks the architecture matches the checkpoint, the calibrator is monotone
and bounded, the catalogue resolves, and a real forward pass returns a well-formed in-range
score. This is what to run after deploying, and after any dependency change.

**In the research repo** — these need `csv/`, `checkpoints/` and the feature cache, so they
live at `scripts/dta_production/` and are deliberately *not* part of the deployable:

| | |
|---|---|
| `test_input_parity.py` | CPU. Prompt token ids + mel features vs the training cache |
| `verify_parity.py` | GPU. Served model vs the checkpoint's own predictions |
| `verify_parity.sh` | sbatch wrapper: smoke → scorer parity → full app path |
| `stage_weights.py` | assemble `weights/` |
| `export_assets.py` | regenerate `assets/` after a retrain |

### Verification status — all passed

Input path (CPU) and end-to-end (GPU, job 19521285, 98 held-out test recordings):

```
ok    prompt token ids identical to training on 98/98 test rows
ok    task embedding ids identical on 98/98 rows
ok    mel features identical (max |diff| 0.0e+00)
ok    strict state-dict load: 0 missing, 0 unexpected keys; 2.75B params
ok    ASR reproduces stored transcripts: 88/98 exact, CER 0.0016
```

| calibrated test RMSE, 98 recordings | | max deviation | systematic bias |
|---|---|---|---|
| model card | 0.3854 | | |
| served, stored transcript | **0.3864** (+0.0009) | 0.016 | −0.00013 |
| served, own ASR — the real app path | **0.3844** (−0.0010) | 0.031 | −0.00157 |

58/98 recordings are bit-identical to the research run; the rest differ by a few bf16 ULPs
because the research eval ran at batch 16 and the service runs at batch 1, so padding widths
and accumulation order differ. The deviations are unbiased (signed mean 0.0001) and cancel:
the aggregate RMSE lands within 0.001 of the research number either way, against a
seed-to-seed sd of 0.010 for this model.

The acceptance gate is on product impact — max deviation ≤ 0.05 (a tenth of a half-level),
|bias| ≤ 0.005, |RMSE delta| ≤ 0.005 — not on per-recording float noise, which is not the
question and cannot be driven to zero across batch sizes.

## 7. Things you need to know before showing a number to a learner

**The CEFR score is calibrated; the four dimensions are not.** They are on the same 0–6 CEFR
scale but are *not* algebraically consistent — the OLS of the shown dims does not equal the
shown CEFR (mean gap 0.217 on test). This is deliberate. Per-dim calibration was measured and
rejected: it worsens 3 of the 4 dims on test under every dev fit set tried (fluency
0.4235→0.4381, pronunciation 0.2783→0.2836, accuracy 0.4261→0.4420), and only `range`
improves. The response carries a `consistency_note` saying so.

**The reportable range is capped at 3.5 (B1+).** Isotonic regression cannot extrapolate past
the labels it was fitted on, so no input can ever score above 3.5 or below 1.14. Speakers
above B1+ are indistinguishable. `clipped_to_calibration_range: true` marks a response where
the raw prediction fell outside the fitted range, so the returned number is a boundary, not a
measurement. **Do not use this model to certify a B2 or above.**

**Audio past 120 s is discarded** (4 × 30 s chunks). `audio.truncated` flags it.

**Domain.** Trained and validated on Finnish L2 speech: DTA/DigiTala, 15–120 s monologue
answers to the prompts in `assets/tasks.json`. Behaviour on other prompts, on dialogue, or on
native speech is unmeasured.

**Test set is 98 recordings from 20 speakers.** Overall RMSE is stable across seeds (sd
0.010). Band-level figures are *not* — seed sd on B1 bias is ~0.10, equal to the entire
spread between different training configurations. Do not read per-level accuracy claims off
this model without seed replicates.

**Calibrated output is lumpy.** Isotonic pools dev rows into 29 blocks, so test scores pile
onto a few values; `<1.5` goes from 11/98 raw to 36/98. The distribution is not smooth even
though the RMSE is good.

Full provenance, metrics and limits: `GET /model_card` or `assets/model_card.json`.

## 8. Layout

```
dta_scorer/          the service
  config.py          frozen geometry + weight paths (not tuning knobs)
  prompt.py          rubric + template — byte-identical to training
  audio.py           resample/chunk/mel — must match training's front end
  asr.py             finnish-v3 Whisper, same decode settings as the training transcripts
  scorer.py          architecture construction + strict checkpoint load
  calibration.py     isotonic knots, no sklearn needed at runtime
  tasks.py           task id → prompt + embedding row
  pipeline.py        end-to-end
  server.py          FastAPI
modeling/            VENDORED from the research repo, byte-identical — do not edit;
                     the state-dict keys depend on these files
assets/              tasks.json, calibration.json, model_card.json (generated)
weights/             staged, gitignored (~15.9 GB)
selftest.py          post-deployment check
score_cli.py         score one file without the server
```

This tree has **no dependency on the research repo** — it can be copied anywhere and the
repo deleted. The build and verification tools live at `scripts/dta_production/` in the
research repo instead, because they read `csv/`, `checkpoints/` and the feature cache.

`modeling/*.py` are exact copies of the research repo's files. Editing them changes
state-dict keys and breaks the load. To regenerate the assets after a retrain, run
`scripts/dta_production/export_assets.py` from the repo root with `CHECKPOINT` pointed at
the new run.

## 9. Audio formats

`soundfile`/libsndfile handles wav, flac, ogg. Browser `MediaRecorder` output (webm/opus,
m4a) is **not** supported directly — a `415` is returned. Convert at the edge:

```bash
ffmpeg -i input.webm -ac 1 -ar 16000 -f wav output.wav
```

Mono 16 kHz is what the model consumes; sending it directly avoids a resample.
