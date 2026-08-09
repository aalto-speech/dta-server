# API reference

These endpoints can be accessed at `http://<host>:<port>/api/v1/docs` when the app is running. See [Development guide](/docs/DEVELOPMENT.md) for local setup and running instructions.

## Endpoints

- `GET /ping`: health check.
- `GET /status`: app status and uptime.
- `POST /analytics/comparison`: cohort comparison stats for a user.
- `POST /request/user`: delete or export request submission.
- `POST /feedback`: feedback submission.
- `POST /speech/assess`: WAV upload and speech scoring.
- `POST /onboarding`: create a user from onboarding data.
- `DELETE /users`: admin user deletion.

> [!TIP]
> Building a client app? [FRONTEND.md](./FRONTEND.md) documents every field, error and
> call order in one place. The live contract is served at `/api/v1/docs` (Swagger UI)
> and `/api/v1/openapi.json`.

## Request notes

- The app is served behind a `/api/v1` root path when using the reverse proxy.
- Most write endpoints accept form data.
- `POST /speech/assess` requires multipart form data with a `.wav` file.
- `DELETE /users` requires header `X-API-Key` and form field `guid`. It erases the
  user's row (related rows follow by FK cascade) **and** their stored recordings
  (`AUDIO_SAVE_DIR/<guid>/`). Recordings go first: if they cannot be removed the call
  fails with `500` and the user row is left in place, so the deletion stays visible and
  can be retried rather than leaving audio nothing points at. It returns `204` for a
  GUID that does not exist, and copies already exported off the server are of course
  unaffected — see [DATA.md](./DATA.md).

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
- `cefr_label` (e.g. `"A2"`) and `cefr_label_fine` (e.g. `"A2+"`) — display
  these in the UI rather than the bare number ("2.1" reads as a mark out of 5).
- `clipped` — `true` means the prediction hit the calibration boundary, so
  `proficiency` is a floor/ceiling value, not a measurement. Show "B1+ or
  above" (or flag for review) instead of presenting the capped number as real.
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

Roughly **1 s of fixed cost + 1 s per 12 s of audio**. Add the learner's own upload time
over mobile data (a 60 s recording is ~1.9 MB). The server processes one recording at a
time — the model holds ~10 GB of VRAM and scoring is serialised — so concurrent requests
queue rather than slow each other down. The app's `ASA_TIMEOUT` (default 60 s) is the
ceiling; on CPU staging the same calls take 30–60 s, which is why staging sets it to 300.

Uploads are rejected above **90 s** of audio (`413 AUDIO_TOO_LONG` since v1.2.0, with
the measured `duration_seconds` in `detail`) or 10 MB (`413 FILE_TOO_LARGE`, with
`size_bytes`; Caddy also enforces 10 MB at the proxy).
