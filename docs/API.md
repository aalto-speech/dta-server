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

## Request notes

- The app is served behind a `/api/v1` root path when using the reverse proxy.
- Most write endpoints accept form data.
- `POST /speech/assess` requires multipart form data with a `.wav` file.
- `DELETE /users` requires header `X-API-Key` and form field `guid`.

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
