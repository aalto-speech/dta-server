# READ THIS FIRST — nothing blocking; one note

The model and this package are finished and verified, and the one item that could have failed
silently — the task-id map — is confirmed. What is left is a units mismatch with no practical
consequence; read item 2 once and move on.

Wiring instructions: `INTEGRATION.md`.

---

## [x] 1. Task-id map — CONFIRMED 2026-08-01

Resolved. `assets/task_id_map.json` now carries `"confirmed_by_deployer": true`, matched
against the app's five tasks by prompt text:

| app task_id | package | task |
|---|---|---|
| 1 | `03_m` | friend asks to borrow 100 euros |
| 2 | `03_n` | voice message: can't come to work |
| 3 | `04_h` | what you do at home |
| 4 | `04_i` | which languages you use and where |
| 5 | `04_test` | describe the shopping picture (dta-task5) |

The sixth DTA task `04_dev` is a development-phase version of the same picture task, not used
in the DTA test or in production, so it is intentionally unmapped. Integer ids outside 1–5
raise `UnknownTask` → HTTP 404.

Worth recording: the pre-confirmation guess had ids 3, 4 and 5 **all wrong**, and a first
correction attempt still had 5 wrong — `04_dev` and `04_test` are *both* picture tasks on the
same image, so the prompt wording cannot separate them; only the `_dev`/`_test` naming does.
Every one of those errors would have scored plausibly with nothing in any log. If the app's
task list changes, re-confirm here before deploying.

## [note] 2. Score scale is CEFR 0–6, dta-server declares 0–5

The model emits CEFR values (0 = `<A1`, 1 = `A1`, 2 = `A2`, 3 = `B1`, 4 = `B2`, 5 = `C1`,
6 = `C2`), not a rating out of 5. dta-server has `Field(ge=0, le=5)` and
`CHECK (... BETWEEN 0 AND 5)`.

**This will not bite.** 5 is C1; the calibrated score is hard-capped at 3.50 (B1+) by the
isotonic calibrator, and DTA is an A1–B1 instrument by design. There is a level and a half of
headroom. Nothing is rejected, nothing is truncated.

Two edits are free, so make them when you are next in those files — they only affect
**new** databases and future validation:

```python
# app/models/speech_assessment.py
Score = Annotated[float, Field(ge=0, le=6)]      # was le=5
```
```sql
-- app/schema.sql, all five score columns
accuracy REAL CHECK (accuracy IS NULL OR (accuracy BETWEEN 0 AND 6)),
```

**Do NOT migrate the existing databases.** `db.py` runs the schema with
`CREATE TABLE IF NOT EXISTS`, so staging and production keep the 0–5 constraint — which is
correct, because SQLite cannot `ALTER` a CHECK. Changing it means rename → recreate → copy →
drop, with `feedback.assessment_id` holding a foreign key onto `assessments`. That is real
risk on live data to relax a bound that can never be reached. Revisit only if a future model
actually scores above 5, which would mean the calibration ceiling was lifted first.

The one thing that genuinely matters for the UI: **show `cefr_label` / `cefr_label_fine`**
(`"A2"`, `"A2+"`), not a bare number. "2.1" reads as a mark out of 5.

**Also:** existing rows hold `random.uniform(0, 5)` placeholders from the stub implementation.
Delete or flag them — they are not comparable to real scores.

## Then clean up (safe, visible if wrong)

- delete `app/utils/whisper_model.py` — the inference container does ASR now, with the
  finnish-v3 model the scorer was trained on
- drop `pytorch`, `openai-whisper`, `torchaudio`, `transformers` from `environment.yaml`,
  then re-lock — this is the payoff for putting the model in its own container

## Non-obvious limit to design around

**The model cannot certify B2 or above.** Calibrated output is capped at 3.50 (B1+); a
genuine B2 speaker returns exactly 3.50, same as a B1+ one. The response sets
`clipped: true` when this happens. Decide what the app shows — suppress, "B1+ or above", or
flag for review — but do not present a capped value as a measurement. `INTEGRATION.md` §7.
