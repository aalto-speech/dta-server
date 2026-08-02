# Frontend integration guide

Everything a client app needs to talk to the DTA server: every endpoint, every field,
every error it can return, and the order the calls happen in. Written against v1.1.2.

**The live contract is also machine-readable.** FastAPI publishes it and the server
serves it:

- Interactive docs: `https://<domain>/api/v1/docs`
- OpenAPI schema: `https://<domain>/api/v1/openapi.json`

Use the schema to generate a typed client if your toolchain can (`openapi-generator`,
`NSwag` for C#/Unity, …). This page exists because the schema alone does not tell you
the *order* of calls, which errors are expected in normal use, or how to present the
scores — and those are where integrations actually go wrong.

## Basics

- Base URL: `https://<domain>/api/v1` — the `/api/v1` prefix is added by the reverse
  proxy; the app itself serves `/ping`, `/speech/assess`, etc.
- **All request bodies are `multipart/form-data` or URL-encoded form fields, not JSON.**
  This is the single most common integration mistake. Only responses are JSON.
- There is no login and no session. Every request identifies the user by a `guid` your
  app generates once (a UUID v4) and stores locally. Treat it as the user's identity:
  if it is lost, their history is unreachable.
- No authentication on user endpoints. `DELETE /users` is admin-only (`X-API-Key`) and
  is not for the app to call — see [User deletion](#user-deletion).
- Request body limit is 10 MB (enforced by the proxy and again by the app).

### Error format

Every error returns the same envelope with an HTTP status:

```json
{ "detail": { "type": "USER_NOT_FOUND", "message": "User not found" } }
```

Branch on `detail.type`, never on the message text. Types you can receive:
`BAD_REQUEST`, `INVALID_API_KEY`, `USER_NOT_FOUND`, `USER_CONSENT_MISSING`,
`UNSUPPORTED_MEDIA_TYPE`, `FILE_TOO_LARGE`, `SCORING_UNAVAILABLE`, `NOT_IMPLEMENTED`,
`DATABASE_CONSTRAINT_ERROR`, `DATABASE_UNAVAILABLE`, `DATABASE_ERROR`,
`INTERNAL_SERVER_ERROR`, `HTTP_ERROR`, `VALIDATION_ERROR`.

FastAPI's own request validation (a missing field, a malformed UUID) returns **422**
with `type: VALIDATION_ERROR` plus an extra `detail.errors` array naming the offending
fields:

```json
{"detail": {"type": "VALIDATION_ERROR", "message": "Invalid request payload",
            "errors": [{"type": "missing", "loc": ["body", "feedback_classification"],
                        "msg": "Field required"}]}}
```

A 422 means the client is wrong, not the user — surface `detail.errors` in your logs
during development, it names the exact field.

## The call sequence

```
first launch ──► POST /onboarding          (once, after consent)
                      │
record audio ──► POST /speech/assess       (per recording; returns scores + transcript)
                      │
                      ├──► POST /feedback              (optional, references assessment_id)
                      └──► POST /analytics/comparison  (optional, cohort position)

user asks to be forgotten ──► POST /request/user (type=delete)
```

---

## `GET /ping` and `GET /status`

Health checks, no parameters. `/ping` returns `{"message": "Pong!"}`. `/status` returns
`{"status": "ok", "env": "production", "uptime_seconds": 1234.5}`. Use `/ping` for a
reachability check at launch; neither requires a user.

## `POST /onboarding`

Creates the user. Call once, after the user accepts consent. **Every later endpoint
returns 404 `USER_NOT_FOUND` until this succeeds.**

Form fields (all required unless marked optional):

| Field | Type | Values |
| --- | --- | --- |
| `guid` | UUID | Generated and stored by your app |
| `consent_accepted` | bool | Must be `true`; the user cannot proceed otherwise |
| `consent_timestamp` | ISO 8601 datetime | e.g. `2026-08-02T10:15:00Z` |
| `background_form_completed` | bool | |
| `background_form_timestamp` | ISO 8601 datetime | |
| `gender` | enum | `woman`, `man`, `other`, `prefer_not_to_answer` |
| `age_group` | enum | `age_18_28`, `age_29_39`, `age_40_50`, `age_51_61`, `age_62_plus` |
| `native_languages` | string or repeated field | At least one; free text (e.g. `Vietnamese`) |
| `other_languages` | string or repeated field | May be empty |
| `moved_to_finland` | string | A 4-digit year ≥ 2015 (e.g. `"2020"`), or the literal `before_2015` |
| `finnish_learning_duration` | enum | `months_0_3`, `months_3_6`, `months_6_9`, `months_9_12`, `years_1_1.5`, `years_1.5_2`, `years_2_2.5`, `years_2.5_3`, `years_3_5`, `years_5_7`, `years_7_10`, `years_10_plus` |
| `finnish_self_assessment` | enum | `A1`, `A2`, `B1`, `B2`, `C1_plus` |
| `app_version` | string, optional | Recorded for analytics; send it |

`native_languages` and `other_languages` accept either one value or the field repeated
once per language (verified against the running server).

Returns **201** with no body. Sending the same `guid` twice returns **409**
`DATABASE_CONSTRAINT_ERROR` — onboard once and persist a local flag. If you do hit 409,
treat it as "already onboarded" and carry on rather than blocking the user.

## `POST /speech/assess`

The main endpoint: uploads a recording, returns scores and the transcript.

`multipart/form-data`:

| Field | Type | Notes |
| --- | --- | --- |
| `guid` | UUID | Must be an onboarded user |
| `task_id` | int | **1–5**, see the task table below |
| `file` | file | WAV, `Content-Type: audio/wav` (or `application/octet-stream`), filename ending `.wav` |
| `description` | string, optional | ≤ 512 characters |

Audio requirements — validated server-side, so enforce them client-side to avoid a
wasted upload: **WAV (PCM)**, **≤ 90 seconds**, **≤ 10 MB**. The model works from
16 kHz mono; anything else is resampled, so recording 16 kHz mono directly keeps
uploads small (~1.9 MB per minute). Only the first 120 s of audio would ever be
considered by the model, which the 90 s limit already stays under.

### Task IDs

| `task_id` | Task | Length |
| --- | --- | --- |
| 1 | Reaction: a friend asks to borrow 100 euros | 30 s |
| 2 | Reaction: voice message explaining why you cannot come | 30 s |
| 3 | Monologue: what you normally do at home | 1 min |
| 4 | Monologue: which languages you use and where | 1 min |
| 5 | Picture: shopping habits | ~1 min |

The task ID selects the task embedding the model scores against, so **sending the wrong
one silently produces a plausible but wrong score** — there is no error. Make sure the
ID travels with the recording through your UI code. IDs outside 1–5 are rejected with
400 `BAD_REQUEST`.

### Response (200)

```json
{
  "assessment_id": 42,
  "scores": {
    "proficiency": 2.41,
    "fluency": 2.26,
    "pronunciation": 2.32,
    "range": 1.35,
    "accuracy": 1.73
  },
  "transcript": "minä asun helsingissä ja opiskelen suomea",
  "cefr_label": "A2",
  "cefr_label_fine": "A2+",
  "clipped": false
}
```

Keep `assessment_id` — feedback references it.

### Working with the scores

Scores are numbers on a **CEFR 0–6 scale** — not marks out of 5:

| 0 | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | --- | --- | --- | --- | --- | --- |
| below A1 | A1 | A2 | B1 | B2 | C1 | C2 |

Three facts that affect how you use them:

1. **`clipped: true` means the number is a boundary, not a measurement.** The model
   cannot resolve above **B1+ (3.5)** or below about **1.14**; when the raw prediction
   falls outside that range `proficiency` is pinned to the edge. Handle this case
   explicitly — a strong speaker scored 3.5 every time has not been measured at 3.5.
2. **Only `proficiency` is calibrated.** `fluency`, `pronunciation`, `range` and
   `accuracy` are raw model outputs on the same scale: indicative, not mutually
   consistent, and they do not average to `proficiency`.
3. `cefr_label` (`"A2"`) and `cefr_label_fine` (`"A2+"`) are precomputed from
   `proficiency` if you want a label rather than a number — no need to derive them
   client-side, and they stay correct if the scale ever changes.

### How long it takes

Measured on the production GPU, end to end including upload on a fast network:

| Recording | Response |
| --- | --- |
| 5 s | 1.7 s |
| 15 s | 2.4 s |
| 30 s | 3.6 s |
| 60 s | 6.0 s |
| 90 s | 8.3 s |

About 1 s fixed plus 1 s per 12 s of audio, plus the user's own upload time on mobile
data. Show a progress indicator; do not block the UI. Set the client timeout to at
least **60 s** — the server gives up on the scorer at 60 s and returns 503.

### Errors

| Status | `type` | Meaning and what to do |
| --- | --- | --- |
| 404 | `USER_NOT_FOUND` | Not onboarded — send the user through onboarding |
| 403 | `USER_CONSENT_MISSING` | Consent was not accepted |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Wrong `Content-Type` on the file part |
| 400 | `BAD_REQUEST` | Not a `.wav` filename, corrupt WAV, or unmapped `task_id` |
| 413 | `FILE_TOO_LARGE` | Over 10 MB or over 90 s |
| 503 | `SCORING_UNAVAILABLE` | Scorer starting up or unreachable — **retryable**, tell the user to try again shortly rather than showing a hard failure |

`503` is the one to design for: it happens for ~30 s after a server restart. A single
automatic retry after a few seconds, then a friendly message, is the right behaviour.

## `POST /feedback`

| Field | Type | Notes |
| --- | --- | --- |
| `guid` | UUID | |
| `feedback_classification` | enum | `self_assessment`, `result_accuracy`, `result_understanding`, `comparison_ui`, `overall_experience` |
| `reaction_value` | int | **1–5** |
| `assessment_id` | int | **Required** for `self_assessment`, `result_accuracy`, `result_understanding`; omit for the other two |
| `comment` | string, optional | ≤ 500 characters |

Returns **201**. Sending `assessment_id` for a non-assessment type, or omitting it for
an assessment type, returns 422.

## `POST /analytics/comparison`

Where the user stands against others at the same self-reported CEFR level.

| Field | Type | Notes |
| --- | --- | --- |
| `guid` | UUID | |
| `days` | int, optional | Window: `14`, `30`, `90`, `180`, `365`, `730`, `1460`. Omit (or send empty) for all-time |

> [!NOTE]
> `days` requires **v1.1.3 or newer**. On v1.1.2 and earlier every value was rejected
> with 422 — form fields arrive as strings and the window enum did not coerce them — so
> all-time (omitting the field) was the only reachable option. If you must support an
> older server, omit `days`.

Success (**200**):

```json
{ "cefr_level": "A2", "cohort_size": 120, "percentile": 0.72, "rank": 34 }
```

`percentile` is 0–1 (0.72 = better than 72% of the cohort), `rank` starts at 1.

**Also 200: the "not available yet" states.** These are normal, not errors, and the app
must render them — early in a study *most* users will see them. Detect them by the
presence of a `status` field:

| `status` | Meaning |
| --- | --- |
| `USER_ASSESSMENT_DATA_INSUFFICIENT` | User needs more scored assessments (includes `required_assessments`, `current_assessments`) |
| `COHORT_SIZE_TOO_SMALL` | Too few comparable users to report without identifying them (includes `cohort_size`) |
| `RANK_UNAVAILABLE` | Rank could not be determined |

Each carries a human-readable `message`. Show your own copy rather than the raw
message, but the shape tells you which case you are in.

## `POST /request/user`

The user exercising their data rights from inside the app.

| Field | Type | Notes |
| --- | --- | --- |
| `guid` | UUID | |
| `type` | enum | `delete` or `export` |

- `delete` → **202 Accepted**: the request is *recorded* for an administrator to action.
  It does not delete anything by itself. Tell the user their request was received and
  will be processed — do not tell them their data is already gone.
- `export` → **501 Not Implemented** (`NOT_IMPLEMENTED`). Not built yet; either hide the
  option or show that it is coming.

## User deletion

`DELETE /users` requires the admin API key and **must not ship in the app** — the key
would be extractable from the binary and lets anyone delete any user. The app's route is
`POST /request/user` above; an administrator performs the deletion server-side, which
erases the database rows and the stored recordings.

## Practical notes for Unity / C#

- Use `UnityWebRequest.Post` with a `List<IMultipartFormSection>`;
  `MultipartFormFileSection(name, bytes, fileName, "audio/wav")` for the recording. Do
  **not** set `Content-Type` manually — the boundary must match.
- Send booleans as `"true"` / `"false"` and timestamps as ISO 8601 with `Z`.
- Set `UnityWebRequest.timeout = 60` or more for `/speech/assess`; the default is often
  too short for a 90 s recording on mobile data.
- Microphone capture: record 16 kHz mono and write a plain PCM WAV header. Unity's
  `AudioClip` is float data — convert to 16-bit PCM before writing, or the server
  rejects it as an invalid WAV.
- Persist the `guid` in `PlayerPrefs` (or better, platform secure storage) on first run
  and never regenerate it.

## Testing against a server

Develop against **staging**, not production: it runs the same API on CPU, so scoring
takes 30–60 s per request instead of 2–8 s — fine for wiring up your integration, and it
keeps test rows out of production. Point the app at production only for timing-sensitive
checks, and use a dedicated test GUID so the rows can be removed afterwards.

Server hostnames are not published in this repository — ask the maintainers for the
staging and production URLs.

A quick end-to-end check from a terminal:

```bash
GUID=$(uuidgen); NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ); BASE=https://<domain>/api/v1

curl -X POST $BASE/onboarding \
  -F "guid=$GUID" -F "app_version=dev" -F "gender=prefer_not_to_answer" \
  -F "age_group=age_29_39" -F "native_languages=English" -F "other_languages=" \
  -F "moved_to_finland=2020" -F "finnish_learning_duration=years_1_1.5" \
  -F "finnish_self_assessment=A2" -F "background_form_completed=true" \
  -F "background_form_timestamp=$NOW" -F "consent_accepted=true" -F "consent_timestamp=$NOW"

curl -X POST $BASE/speech/assess \
  -F "file=@recording.wav;type=audio/wav" -F "guid=$GUID" -F "task_id=2"
```
