# API reference

These endpoints can be accessed at `http://<host>:<port>/api/v1/docs` when the app is running. See [Development guide](/docs/DEVELOPMENT.md) for local setup and running instructions.

## Endpoints

- `GET /ping`: health check.
- `GET /status`: app status and uptime.
- `POST /analytics/comparison`: cohort comparison stats for a user. Returns raw
  `percentile`/`rank` for analysis plus a `display` object holding the bucketed position
  the app is meant to render — see [Cohort position](#cohort-position).
- `POST /feedback`: feedback submission (upserts on `guid` + `assessment_id` + type).
- `POST /speech/assess`: WAV upload and speech scoring.
- `POST /onboarding`: create a user from onboarding data.
- `PATCH /users/level`: move the CEFR level a user is working at.
- `DELETE /users`: user deletion, requires `X-Delete-Key`.

`POST /request/user` was removed in v1.3.0. It took a `type` of `delete` or `export`;
this app does not export user data, and deletion is `DELETE /users`.

> [!TIP]
> Building a client app? [FRONTEND.md](./FRONTEND.md) documents every field, error and
> call order in one place. The live contract is served at `/api/v1/docs` (Swagger UI)
> and `/api/v1/openapi.json`.

## Request notes

- The app is served behind a `/api/v1` root path when using the reverse proxy.
- Most write endpoints accept form data.
- `POST /speech/assess` requires multipart form data with a `.wav` file.
- `DELETE /users` requires header `X-Delete-Key` (matching `SERVER_DELETE_KEY`) and
  form field `guid`. It erases the
  user's row (related rows follow by FK cascade) **and** their stored recordings
  (`AUDIO_SAVE_DIR/<guid>/`). Recordings go first: if they cannot be removed the call
  fails with `500` and the user row is left in place, so the deletion stays visible and
  can be retried rather than leaving audio nothing points at. It returns `204` for a
  GUID that does not exist, and copies already exported off the server are of course
  unaffected — see [DATA.md](./DATA.md).

## Cohort position

`POST /analytics/comparison` returns the learner's position within their CEFR cohort in
two forms. `percentile` (0–1) and `rank` are the raw values, kept for research; the
`display` object is what the app shows:

```json
"display": { "top_percent": 25, "top_rank": 50 }
```

- `top_percent` ∈ `{1, 5, 10, 25, 50, null}` — "top N %"
- `top_rank` ∈ `{1, 2, 3, 5, 10, 25, 50, 100, null}` — "top N"
- `null` on either means **display nothing** for that form. Both are `null` below the
  50th percentile; `top_rank` is `null` past #100.

Rungs are spaced by ratio rather than by even steps, because perceived difference between
positions scales with ratio — #9 → #10 is a real step, #109 → #110 is not. Every bucket
rounds **up**, away from the learner, so a displayed claim is always true: 3.4 % becomes
"top 5 %", never "top 3 %". Below the halfway mark nothing is claimed at all, which
leaves the learner with the criterion-referenced information (score and CEFR band)
instead of a verdict against peers.

Like the CEFR label, **the server owns this** — the ladders are in `app/utils/ranking.py`
and revising them reaches every installed app without a client release. Clients must not
derive a position from `rank` or `percentile`.

Ties use competition rank (equal averages share the better rank: 1, 2, 2, 4). Before
v1.3.0 ties were broken by `guid ASC`, which permanently ordered two identical learners
by an accident of their identifiers.

The rationale for keeping this display secondary to the score and band — and the items
deliberately deferred — is in [FUTURE_PLAN.md](./FUTURE_PLAN.md).

## Speech assessment scores

`POST /speech/assess` scores are produced by the M-CASA model (see
[../inference/README.md](../inference/README.md)) and are **CEFR values on a
0–6 scale** — not marks out of 5:

| value | 0 | 1 | 2 | 3 | 4 | 5 | 6 |
| ----- | --- | --- | --- | --- | --- | --- | --- |
| CEFR | <A1 | A1 | A2 | B1 | B2 | C1 | C2 |

Response fields and what to do with them:

- `scores.proficiency` — the holistic CEFR score, **calibrated**; this is the
  number the model actually stands behind. It is hard-capped at **3.5 (B1+)**:
  the model cannot certify B2 or above.
- `scores.fluency` / `pronunciation` / `range` / `accuracy` — raw model
  outputs on the same scale, **not calibrated** and not algebraically consistent
  with `proficiency`. Treat as indicative.
- `cefr_label` and `cefr_label_fine` — display these rather than the bare number
  ("2.1" reads as a mark out of 5). **Production emits exactly four labels**:
  `A1` `[0,1.55)`, `A2` `[1.55,2.40)`, `A2+` `[2.40,2.75)`, `B1` `[2.75,…)`, floored into the band
  rather than rounded to the nearest one. `<A1`, `A1+` and `B1+` are never sent, and
  dimension labels are capped at `B1` the same way. The app tier re-derives all of them
  from the scores in `app/utils/cefr.py` and discards the inference container's own
  labels, which use the research convention over the full scale. The banding belongs to
  this model version and changes when a model earns the finer scale.
- `clipped` — `true` means the prediction hit the calibration boundary, so
  `proficiency` is a floor/ceiling value, not a measurement. Show "B1+ or
  above" (or flag for review) instead of presenting the capped number as real.
- `content` (since v1.2.0) — whether the answer addressed the task:
  `{relevance: on_topic|partial|off_topic, confidence, reason, judge}`, from a
  separate zero-shot check run after scoring. Stored on the row as
  `content_relevance` / `content_confidence`. **`null` means "not checked", not "off
  topic"** — it fails open by design.
  **The verdict never changes the scores.** v1.2.0 zeroed all five on `off_topic`;
  v1.3.0 reverted that, because the judge was measured tracking answer length and ASR
  quality rather than topic (the same on-topic content scores p(bad)=0.59 at three words
  and 0.02 at twenty), so the zeroing fell on A1 learners and destroyed the evidence
  needed to notice. With the real scores stored, a wrongly flagged recording can be found
  by comparing the score against the flag — that query is the point.
  For analysis, treat `content_relevance` as a flag to filter on, not as a fact.
  See `inference/dta_scorer/relevance.py` for what the judge does and does not catch.
- Requests fail with `503 SCORING_UNAVAILABLE` while the scorer is starting
  (~20 s on GPU, ~10 min on CPU staging) or unreachable, and with
  `400 BAD_REQUEST` for a `task_id` with no mapped speaking task (valid ids: 1–5).

### How long a learner waits

Measured end to end on production (Tesla P100, fp16) 2026-08-02 — from HTTPS request to
response, including upload on the local network, scoring, and the database write:

| Recording length | Response time |
| ---------------- | ------------- |
| 5 s              | 1.7 s         |
| 15 s             | 2.4 s         |
| 30 s             | 3.6 s         |
| 60 s             | 6.0 s         |
| 90 s (the max)   | 8.3 s         |

v1.2.0 adds ~0.7 s at every length for the `content` relevance check (one extra prefill
on the Qwen already in VRAM); set `DTA_RELEVANCE_CHECK=0` to buy that back.

Roughly **1 s of fixed cost + 1 s per 12 s of audio**. Add the learner's own upload time
over mobile data (a 60 s recording is ~1.9 MB). The server processes one recording at a
time — the model holds ~10 GB of VRAM and scoring is serialised — so concurrent requests
queue rather than slow each other down. The app's `ASA_TIMEOUT` (default 60 s) is the
ceiling; on CPU staging the same calls take 30–60 s, which is why staging sets it to 300.

Uploads are rejected above **90 s** of audio (`413 AUDIO_TOO_LONG` since v1.2.0, with
the measured `duration_seconds` in `detail`) or 10 MB (`413 FILE_TOO_LARGE`, with
`size_bytes`; Caddy also enforces 10 MB at the proxy).
