# Frontend integration guide

Everything a client app needs to talk to the DTA server: every endpoint, every field,
every error it can return, and the order the calls happen in. Written against **v1.2.0**.
Servers report their version in `GET /status` (`version`) and in OpenAPI `info.version` --
feature-detect against that instead of guessing (both were added in v1.2.0; on older
servers the `version` key is simply absent).

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
- No authentication on user endpoints. `DELETE /users` is maintainer-only (`X-Delete-Key`) and
  is not for the app to call — see [User deletion](#user-deletion).
- Request body limit is 10 MB (enforced by the proxy and again by the app).

### Error format

Every error returns the same envelope with an HTTP status:

```json
{ "detail": { "type": "USER_NOT_FOUND", "message": "User not found" } }
```

Branch on `detail.type`, never on the message text. Types you can receive:
`BAD_REQUEST`, `INVALID_API_KEY`, `USER_NOT_FOUND`, `USER_CONSENT_MISSING`,
`UNSUPPORTED_MEDIA_TYPE`, `FILE_TOO_LARGE`, `AUDIO_TOO_LONG`, `SCORING_UNAVAILABLE`,
`NOT_IMPLEMENTED`, `DATABASE_CONSTRAINT_ERROR`, `DATABASE_UNAVAILABLE`,
`DATABASE_ERROR`, `INTERNAL_SERVER_ERROR`, `HTTP_ERROR`, `VALIDATION_ERROR`.

Since v1.2.0 this envelope is **published in the OpenAPI schema** (`ErrorEnvelope`,
referenced from every endpoint's error responses), so generated clients can parse it.
Some errors carry extra machine-readable keys inside `detail` next to `type`/`message`
-- they are listed with their endpoints below. The commitment on shapes: **422 is the
same object plus an `errors` array; every other status is the plain object.**

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
`{"status": "ok", "env": "production", "version": "1.2.0", "uptime_seconds": 1234.5}` --
`version` is the release the server runs (absent before v1.2.0) and equals OpenAPI
`info.version`. Use `/ping` for a reachability check at launch and `version` for feature
detection; neither requires a user.

## `POST /onboarding`

Creates the user. Call once, after the user accepts consent. **Every later endpoint
returns 404 `USER_NOT_FOUND` until this succeeds.**

Since v1.2.0 the fields are in **two tiers**, so the background form can change over the
life of the study without breaking account creation:

**Required — missing any of these is a 422:**

| Field | Type | Values |
| --- | --- | --- |
| `guid` | UUID | Generated and stored by your app |
| `consent_accepted` | bool | Must be `true`; without consent there is no user |
| `consent_timestamp` | ISO 8601 datetime | e.g. `2026-08-02T10:15:00Z` |
| `finnish_self_assessment` | enum | `A1`, `A2`, `B1`, `B2`, `C1_plus` — defines the cohort `/analytics/comparison` ranks against, which is why it is required. Dropping or renaming it needs a coordinated release. |

**Research metadata — optional. Omitted or sent empty stores null; never a 422:**

| Field | Type | Values |
| --- | --- | --- |
| `gender` | enum | `woman`, `man`, `other`, `prefer_not_to_answer` |
| `age_group` | enum | `age_18_28`, `age_29_39`, `age_40_50`, `age_51_61`, `age_62_plus` |
| `native_languages` | string or repeated field | Free text (e.g. `Vietnamese`); newline-separated also accepted |
| `other_languages` | string or repeated field | Free text; may be empty |
| `moved_to_finland` | string | A 4-digit year ≥ 2015, or `before_2015`. No longer collected by the app since 2.0.0; historical rows keep their values |
| `finnish_learning_duration` | enum | `months_0_3` … `years_10_plus`. No longer collected since app 2.0.0 |
| `background_form_completed` | bool | |
| `background_form_timestamp` | ISO 8601 datetime | |
| `app_version` | string | Recorded for analytics; please send it |

**Unknown fields are ignored, not rejected** — a newer app can start sending a question
before the server knows it. (They are not stored yet; promoting them to columns is a
server release.) Values that ARE provided are still validated: a wrong enum value is
still a 422 — the tiering forgives absence, not garbage. There is deliberately no
server-side defaulting (a fabricated `A1` would corrupt the cohort for everyone else).

`native_languages` and `other_languages` accept either one value or the field repeated
once per language (verified against the running server).

Returns **201** with no body. Sending the same `guid` twice returns **409**
`DATABASE_CONSTRAINT_ERROR` — onboard once and persist a local flag. If you do hit 409,
treat it as "already onboarded" and carry on rather than blocking the user.

On servers **older than v1.2.0** all metadata fields are required — an app that has
stopped collecting them cannot onboard new users there. Feature-detect via `/status`.

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
  "task_id": 4,
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
  "dimension_labels": {
    "fluency":       {"label": "A2", "label_fine": "A2+"},
    "pronunciation": {"label": "A2", "label_fine": "A2+"},
    "range":         {"label": "A1", "label_fine": "A1+"},
    "accuracy":      {"label": "A1", "label_fine": "A2"}
  },
  "clipped": false,
  "content": {
    "relevance": "on_topic",
    "confidence": 0.97,
    "reason": null,
    "judge": "dta-relevance-v1"
  }
}
```

Keep `assessment_id` — feedback references it.

**`task_id` (added v1.2.0) is the id that was actually scored — assert it equals what
you sent** and fail loudly on a mismatch. A mis-wired task otherwise produces a
plausible wrong score that no one can detect later, in the app or in the database.

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
   `proficiency`; since v1.2.0 `dimension_labels` carries the same two labels for each
   analytic dimension, so a five-row results screen derives nothing locally.

### Exactly how the labels are derived (agree with this or your stars will contradict them)

- `label` (coarse) **FLOORS** to the band: 2.9 → `"A2"`, not B1.
- `label_fine` **ROUNDS to the nearest half step**: 2.3 → 2.5 → `"A2+"`; 2.75 → 3.0 →
  `"B1"`. It is **not** a floor-based interval — a client that renders `[2.5, 3.0)` as
  its "A2+" tier will disagree with the server's label on scores in `[2.25, 2.5)` and
  `[2.75, 3.0)`. If you compute star tiers from the number, use the same
  round-to-nearest-half rule, or compute the tier from `label_fine` itself.
- With the current model, calibrated `proficiency` lives in **[1.14, 3.5]**, so
  `cefr_label_fine` can only be one of **A1, A1+, A2, A2+, B1, B1+** — and a
  clipped-high result is exactly 3.5, i.e. `"B1+"`. Anything above is unreachable until
  the model itself improves. `dimension_labels` are computed from the **raw** scores, so
  they can span the whole scale — cap them in the UI the same way you cap proficiency.
- **Do not present A1+ or B1+ as distinctions to the learner.** They are reachable
  values, so your code must accept them, but the model owner's position is that the
  current model is not reliable at that resolution near the ends of its range: A1+ comes
  from a narrow band of calibration knots, and B1+ is simply the clipped ceiling. Fold
  **A1+ → A1** and **B1+ → B1** for display. The four tiers worth showing today are
  **A1, A2, A2+, B1**; a later model may earn the finer scale.

### `content` — did the answer address the task? (added v1.2.0)

The scorer rates **how** someone speaks, not **what** they said, so a fluent answer to
the wrong question scores well. `content` is a separate check that reads the task prompt
and the transcript and returns one of three verdicts.

| `relevance` | Meaning | Suggested UI |
| --- | --- | --- |
| `on_topic` | The answer addresses the task. | Show the result normally. |
| `partial` | On the topic but incomplete, or it drifts. | Show the result, with a note that it only partly addressed the task. |
| `off_topic` | The answer does not address the task at all. | Withhold the score and offer the task again. |

`confidence` is the judge's probability for the verdict it gave (0–1). `reason` is a
short English string, fixed per verdict rather than generated — **localise from
`relevance`, not from `reason`**. `judge` identifies the prompt version behind the
verdict.

Four rules, all load-bearing:

1. **`content` may be `null` or absent — that means "not checked", never "off topic".**
   The check fails open (older server, judge disabled, judge errored). Show the score.
2. **It never changes the scores.** The numbers are identical with and without it. If
   you withhold a result on `off_topic`, you are choosing not to show a score that was
   computed normally — so keep `assessment_id` and still let feedback reference it.
3. **`off_topic` already clears a confidence bar server-side.** A verdict the judge was
   unsure of is downgraded to `partial`, because withholding a real learner's score is
   worse than missing an off-topic answer. Do not add a second threshold on
   `confidence`; branch on `relevance`.
4. **Prefer "try again" over "you failed".** An empty or silent recording also returns
   `off_topic` (with `reason` naming the no-speech case), and that is the most common
   way a learner will meet this.

What it does **not** do: separate two tasks in the same everyday domain. A shopping
answer given to the "what do you do at home" task is measured to pass as on-topic. It
reliably catches a different subject, silence, and answers spoken in another language
(ASR is pinned to Finnish, so English comes through as word salad).

The judge has been smoke-tested, not validated against labelled Finnish data. Treat
`off_topic` as good enough to prompt a retry, not as a verdict to argue with a learner
about; the plan is to label a sample of real `content_relevance` values and measure
before it is trusted further.

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
data. Show a progress indicator; do not block the UI.

**Add ~0.7 s from v1.2.0** for the `content` check above, at every length — measured on
the production GPU. It runs after scoring, so the numbers were already computed when it
is spent.

**The shared timeout number, owned by both sides: the server gives up on the scorer at
60 s** (`ASA_TIMEOUT`, production default) and returns a retryable 503. The client must
wait longer than that — 90 s is right — or the user sees a transport error instead of
the 503 they could act on. If this number ever changes it changes here first.

### Errors

| Status | `type` | Meaning and what to do |
| --- | --- | --- |
| 404 | `USER_NOT_FOUND` | Not onboarded — send the user through onboarding |
| 403 | `USER_CONSENT_MISSING` | Consent was not accepted |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Wrong `Content-Type` on the file part |
| 400 | `BAD_REQUEST` | Not a `.wav` filename, corrupt WAV, or unmapped `task_id` |
| 413 | `FILE_TOO_LARGE` | Over 10 MB. `detail` carries `size_bytes` and `max_size_bytes` — a short recording landing here means the client wrote the WAV at the wrong rate or depth |
| 413 | `AUDIO_TOO_LONG` | Over 90 s (v1.2.0; was `FILE_TOO_LARGE` before). `detail` carries `duration_seconds` and `max_duration_seconds` — arriving here at all means client and server disagree about the recording's length |
| 503 | `SCORING_UNAVAILABLE` | See below — `detail.reason` says which case you are in |

**503 carries a `reason` since v1.2.0**, plus a `Retry-After` header when a retry makes
sense:

| `detail.reason` | Means | `Retry-After` | Client behaviour |
| --- | --- | --- | --- |
| `starting_up` | Scorer answering but its model is still loading (~30 s after a restart) | 30 | Auto-retry once after a few seconds |
| `busy` | Scoring call timed out — scorer alive but overloaded | 60 | Retry with a longer wait |
| `unreachable` | Scorer down; retrying will not help | absent | Friendly failure, no auto-retry |

On older servers `reason` is absent — treat that as `starting_up` (the old advice).

## `POST /feedback`

| Field | Type | Notes |
| --- | --- | --- |
| `guid` | UUID | |
| `feedback_classification` | enum | `self_assessment`, `result_accuracy`, `result_understanding`, `comparison_ui`, `overall_experience` |
| `reaction_value` | int | **1–5** (emoji sentiment — do not confuse with the 0–6 CEFR scale) |
| `assessment_id` | int | **Required** for `self_assessment`, `result_accuracy`, `result_understanding`; **must be genuinely absent** — not 0, not null — for `comparison_ui` and `overall_experience` |
| `comment` | string, optional | ≤ 500 characters |

Returns **201**. The pairing rule is deliberate, stable, and **enforced in both
directions since v1.2.0**: omitting the id for an assessment type, or sending it for an
app-scoped type, both return 422 with a message naming the rule. (Before v1.2.0 the
second direction was unenforced: a stray id either tripped a 409 foreign-key error or
was silently stored as a link to an unrelated assessment.) Rows for `comparison_ui` /
`overall_experience` carry the `guid`, so they are attributable and queryable per user
without an assessment link.

**Naming note for anyone analysing the data:** `self_assessment` does NOT mean the user
assessing their own CEFR level. The question shown under that classification is *"How
did you find this task?"* on a five-emoji scale — a task reaction. The wire value is
kept for compatibility with shipped clients; treat the name as historical.

## `POST /analytics/comparison`

Where the user stands against others at the same self-reported CEFR level.

| Field | Type | Notes |
| --- | --- | --- |
| `guid` | UUID | |
| `days` | int, optional | Window: `14`, `30`, `90`, `180`, `365`, `730`, `1460`. Omit (or send empty) for all-time |

> [!NOTE]
> `days` requires **v1.2.0 or newer**. On v1.1.2 and earlier every value was rejected
> with 422 — form fields arrive as strings and the window enum did not coerce them — so
> all-time (omitting the field) was the only reachable option. On an older server, omit
> `days`; feature-detect via `/status` `version`.

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

**The commitment (v1.2.0, and true of every earlier release): `status` is ALWAYS
present on the unavailable shapes and NEVER present on a successful comparison.**
Branch on that — not on sentinel values like a negative `percentile`, which would break
silently if a field were renamed. All four response models are published in the OpenAPI
schema since v1.2.0.

## `POST /request/user`

The user exercising their data rights from inside the app.

| Field | Type | Notes |
| --- | --- | --- |
| `guid` | UUID | |
| `type` | enum | `delete` or `export` |

Optional header: `X-Client-Key` — validated only when the server configures
`CLIENT_API_KEY` (403 `INVALID_API_KEY` on mismatch). It is a guardrail against
scripted abuse, not a security boundary: it ships inside the app binary.

It is a **different credential from the delete key** and opens far less. `X-Client-Key`
lets a caller *ask* for the deletion of a guid they already hold; erasing data outright
needs `X-Delete-Key` on `DELETE /users`, which never leaves the server. Do not set them
to the same value — the app key is recoverable from a decompiled build.

**`delete` deletes immediately (since v1.2.0)** — recordings first, then database rows
— and always answers **202** with the outcome in the body:

| Body | Meaning | What to tell the user |
| --- | --- | --- |
| `{"status": "deleted", ...}` | Data is gone from the server | "Your data has been deleted." |
| `{"status": "pending", ...}` | The deletion failed; it is logged with the guid for a maintainer | "Removal from our servers is underway." |
| no `status` key | Server older than v1.2.0: request was parked for an admin | cautious wording; do not claim deletion happened |

Same 202 either way — retry logic keys off the HTTP status alone, and any 2xx means
"the request arrived, stop retrying". Every attempt (success or failure) is logged
server-side with the guid, timestamp and cause, so failed deletions are auditable even
after the app has discarded the guid.

- Deletion erases **both** the database rows and the stored audio recordings.
- **A deleted guid can be re-onboarded** — the guid is not burned. Generating a fresh
  guid and re-onboarding is equally fine (that person then appears twice in the data
  over time, which the study accepts). A "start over" flow on 404 `USER_NOT_FOUND`
  needs no server support.
- `export` → **501 Not Implemented** (`NOT_IMPLEMENTED`). Not built yet; either hide the
  option or show that it is coming.

## User deletion (maintainer)

`DELETE /users` requires the server's delete key (header `X-Delete-Key`, matching
`SERVER_DELETE_KEY`; both were called "admin" before v1.2.0) and **must not ship in the
app** — the key would be extractable from the binary and lets anyone delete any user.
Note this is a different credential from `X-Client-Key`, which the app may send on
`POST /request/user` and which can only ask for a deletion, never perform one. It erases the
rows and the recordings, recordings first (a failure leaves the user row in place so
the deletion stays visible and retryable). The app's route is `POST /request/user`
above, which performs the same deletion with the same guarantees.

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
