# Staging deployment — every step, nothing elided

Two stages on purpose:

* **Stage 1** gets the inference container running and verified, with the app **untouched**.
  If anything is wrong you find out before it can affect a request.
* **Stage 2** patches the app to call it, replacing the `random.uniform(0, 5)` placeholder.

Production is not touched by any of this. It updates only when you cut a GitHub release off
`main`, which is covered at the end.

Conventions below: `[LOCAL]` = your machine / dev clone, `[STAGING]` = ssh'd into the staging
server. Every command is written out in full.

---

# STAGE 1 — inference container only

## 1.1 [LOCAL] Get the package

From Triton:

```bash
scp <you>@triton.aalto.fi:/scratch/elec/t412-slaam/ASA-LLM/dta_inference_code.zip .
```

## 1.2 [LOCAL] Put it in the repo on the `dev` branch

CI builds `:staging` images from `dev`, not `main`.

```bash
git clone https://github.com/aalto-speech/dta-server.git
cd dta-server
git checkout dev
git pull

unzip ../dta_inference_code.zip -d .
ls inference/          # expect: ACTION_REQUIRED.md Containerfile INTEGRATION.md README.md
                       #         assets/ compose.inference.yaml deploy/ docs/ dta_scorer/
                       #         environment.yml modeling/ requirements.txt score_cli.py selftest.py
```

## 1.3 [LOCAL] CD workflow — already done in this repo

> **Status: applied.** The two-image CD workflow now lives at
> `.github/workflows/cd.yaml` (with the paths-filter expression fixed to compare
> `== 'true'`). There is no separate copy under `inference/deploy/` anymore.

## 1.4 [LOCAL] Inference service in `compose.yaml` — already done in this repo

> **Status: applied.** `compose.yaml` now carries the `inference` service with the
> image tag parameterised as `${DTA_TAG:-latest}` (so staging boxes select
> `:staging` via the env file instead of editing the file), `DTA_DEVICE`
> parameterised, `ASA_URL`/`ASA_TIMEOUT` on the `dta` service, and the external
> `asa-weights` volume. `inference/compose.inference.yaml` remains only as a
> historical reference fragment — `compose.yaml` is canonical.

Two properties worth preserving when editing:

- There is **no `ports:`** on `inference`. Only the app needs to reach it, over the compose
  network. Publishing it would expose an unauthenticated scoring endpoint.
- The nvidia `deploy.resources` GPU reservation is **commented out** in `compose.yaml`;
  uncomment it on GPU (production) hosts. It stays commented on staging because podman-compose
  handles device reservations unreliably on hosts without the nvidia toolkit.

## 1.5 [LOCAL] Commit and push

```bash
git add inference .github/workflows/cd.yaml compose.yaml
git commit -m "Add M-CASA inference container"
git push origin dev
```

## 1.6 [LOCAL] Wait for the build

https://github.com/aalto-speech/dta-server/actions — the inference image is ~10 GB and takes
several minutes longer than the app image. Wait for green before continuing.

Result: `ghcr.io/aalto-speech/dta-server/inference:staging`.

## 1.7 [STAGING] Connect

```bash
ssh -i ~/.ssh/keyname.pem <user>@<staging-floating-ip>
cd ~/dta
```

## 1.8 [STAGING] Update `compose.yaml`

Config files are not delivered by the image — your `UPDATING.md` fetches them with `curl`:

```bash
cp compose.yaml compose.yaml.bak
curl -fsSL -o compose.yaml \
  https://raw.githubusercontent.com/aalto-speech/dta-server/dev/compose.yaml
diff compose.yaml.bak compose.yaml
```

No tag editing needed: the image tags are parameterised as `${DTA_TAG:-latest}`, and step 1.9
sets `DTA_TAG=staging` in the env file.

> [!WARNING]
> **Memory on CPU staging.** The model holds ~10 GB of RAM when `DTA_DEVICE=cpu`. Running
> `selftest.py` via `podman exec` loads a **second** full copy next to the serving one —
> on a 20 GB host this fills RAM and swap and can OOM the server (observed). On CPU
> staging, run selftest *instead of* the server:
>
> ```bash
> podman stop dta-inference
> podman run --rm -v asa-weights:/weights:ro -e DTA_WEIGHTS_DIR=/weights \
>     -e DTA_DEVICE=cpu ghcr.io/aalto-speech/dta-server/inference:staging python selftest.py
> systemctl --user restart dta-compose.service
> ```
>
> On GPU production the model lives in VRAM, so `podman exec dta-inference python selftest.py`
> is fine as documented.

## 1.9 [STAGING] Select staging images and force CPU mode

Staging has no usable GPU. Without `DTA_DEVICE=cpu` the container fails at model load.

```bash
{
  echo 'DTA_TAG=staging'      # run :staging images, not :latest
  echo 'DTA_DEVICE=cpu'       # no GPU on this box
  echo 'ASA_TIMEOUT=300'      # CPU scoring takes 30-60 s; keep the app waiting
} >> ~/.config/dta/env
cat ~/.config/dta/env
```

## 1.10 [STAGING] Fetch the weights

Not in the image (~15.9 GB). Public repo, so no token needed.

```bash
curl -fsSL -o fetch_weights.sh \
  https://raw.githubusercontent.com/aalto-speech/dta-server/dev/inference/deploy/fetch_weights.sh
chmod +x fetch_weights.sh
./fetch_weights.sh
```

Takes a while. Resumable — if it dies, run it again and finished files are skipped.

Verify:

```bash
du -sh "$(podman volume inspect -f '{{.Mountpoint}}' asa-weights)"/*
# expect roughly: 4.6G qwen_base, 8.3G scorer, 3.1G whisper_finnish_v3, plus assets
```

## 1.11 [STAGING] Pull the image and restart

```bash
podman pull ghcr.io/aalto-speech/dta-server/inference:staging
systemctl --user restart dta-compose.service
podman ps
```

## 1.12 [STAGING] Watch it load — this takes ~10 MINUTES on CPU

```bash
podman logs -f dta-inference
```

Wait for:

```
INFO dta_scorer: loading model on cpu (~10 GB, expect ~20 s) ...
INFO dta_scorer: ready: finnish-v3_le40_mcasa_noac_eqcap2x_ttsfluall3_s2022
```

The "expect ~20 s" is the GPU figure. On CPU it is about ten minutes. It is not hung.

## 1.13 [STAGING] Verify

```bash
podman exec dta-inference curl -s http://localhost:8000/health
```

Expect `"status": "ok"` and the checkpoint name. Then the full self-test:

```bash
podman exec dta-inference python selftest.py
```

Every line should read `ok`, ending in `PASS`, followed by the OUTSTANDING reminders (those
are expected — they are Stage 2 work, see below).

If it says **WEIGHTS DO NOT MATCH THIS IMAGE**, the volume and the image are out of step:
re-run `./fetch_weights.sh`, or pull the image again. Do not bypass it.

## 1.14 [STAGING] Score one real recording

```bash
podman exec dta-inference curl -s -X POST http://localhost:8000/score \
  -F audio=@/path/to/some.wav -F task_id=1
```

Expect a JSON body with `transcript`, `cefr.score`, `cefr.label`, and four `dimensions`.
**On CPU this takes 30–60 seconds.** That is normal and does not reflect production.

**Stage 1 is complete.** The app is still returning random scores; nothing user-facing changed.

---

# STAGE 2 — wire the app to it

Full detail in `inference/INTEGRATION.md`. Order matters.

## 2.1 [LOCAL] Task-id map — already confirmed, just sanity-check it

Confirmed 2026-08-01 against the app's five tasks (1 borrow money, 2 can't come to work,
3 at home, 4 languages, 5 shopping picture). Nothing to do unless the app's task list has
changed since:

```bash
cat inference/assets/task_id_map.json      # confirmed_by_deployer: true
```

If the app ever gains or reorders tasks, re-confirm here **before** deploying. A wrong map
scores every recording plausibly and wrongly with no error anywhere — the original guess had
three of the five wrong, and `04_dev` vs `04_test` cannot be told apart from the prompt text
(both are the same picture task; only the `_dev`/`_test` naming separates them).

## 2.2 [LOCAL] Copy the client

```bash
cp inference/dta_scorer/client.py app/utils/asa_client.py
```

It imports only `httpx`, already in your `conda-lock.yaml`.

## 2.3 [LOCAL] Add the setting

In `app/config.py`, add to `Settings`:

```python
    asa_url: str
```
and in the loader:
```python
    asa_url=os.getenv("ASA_URL", "http://inference:8000"),
```

## 2.4 [LOCAL] Replace the scoring body

In `app/services/speech_assessment_service.py`: delete `_transcribe()` and the five
`round(uniform(0, 5), 1)` lines, remove the `from random import uniform` and
`from app.utils.whisper_model import get_transcriber` imports, then add:

```python
from fastapi import HTTPException
from app.utils.asa_client import ASAClient, ASAError

_asa = ASAClient(base_url=SETTINGS.asa_url)
```

and inside `assess_speech_request`, after the audio validators and the file write:

```python
try:
    result = await _asa.assess(content, task_id=data.task_id,
                               filename=data.file.filename or "audio.wav")
except ASAError as e:
    logger.error("ASA scoring failed for user %s: %s", data.guid, e)
    raise HTTPException(status_code=503, detail="scoring temporarily unavailable") from e

transcript = result["transcript"]
accuracy = result["scores"]["accuracy"]
fluency = result["scores"]["fluency"]
proficiency = result["scores"]["proficiency"]
pronunciation = result["scores"]["pronunciation"]
range_score = result["scores"]["range"]
```

The rest of the function — `create_assessment`, the response — is unchanged.

## 2.5 [LOCAL] Widen the score scale to 0–6 — optional, new databases only

`app/models/speech_assessment.py`:

```python
Score = Annotated[float, Field(ge=0, le=6)]      # was le=5
```

`app/schema.sql`, all five score columns:

```sql
accuracy REAL CHECK (accuracy IS NULL OR (accuracy BETWEEN 0 AND 6)),
```

**Do not migrate existing databases for this.** `db.py` runs the schema with
`CREATE TABLE IF NOT EXISTS`, so staging and production keep 0–5 — which is fine: 5 is C1 and
the calibrated score is capped at 3.50 (B1+), so the bound cannot be reached. SQLite cannot
`ALTER` a CHECK constraint, so changing it on live data means rename → recreate → copy → drop,
with `feedback.assessment_id`'s foreign key to work around. Not worth it. Skip this step
entirely if you prefer; it only makes *future* deployments tidier.

## 2.6 [LOCAL] Drop the now-unused ASR

```bash
git rm app/utils/whisper_model.py
```

Remove from `environment.yaml`: `pytorch`, `openai-whisper`, `torchaudio`, `transformers`.
Then re-lock:

```bash
conda-lock lock -f environment.yaml -p linux-64
```

Also delete `tests/test_whisper_model.py`, and update
`tests/test_speech_assessment_endpoint.py` to mock `ASAClient.assess` instead of the
transcriber.

## 2.7 [LOCAL] Push

```bash
git add -A
git commit -m "Use M-CASA inference service for speech assessment"
git push origin dev
```

## 2.8 [STAGING] Deploy and test end to end

```bash
podman pull ghcr.io/aalto-speech/dta-server:staging
systemctl --user restart dta-compose.service
podman logs -f dta caddy
```

Then POST a real recording to `/speech/assess` and confirm the scores are no longer random,
the transcript is Finnish, and the row lands in the database.

---

# PRODUCTION — only when staging is proven

```bash
# [LOCAL] merge dev into main, then create a GitHub release (builds :latest for BOTH images)

# [PRODUCTION]
ssh -i ~/.ssh/keyname.pem <user>@<production-floating-ip>
cd ~/dta

cp compose.yaml compose.yaml.bak
curl -fsSL -o compose.yaml \
  https://raw.githubusercontent.com/aalto-speech/dta-server/main/compose.yaml
# leave DTA_TAG unset in ~/.config/dta/env — unset means :latest (production)
# do NOT set DTA_DEVICE=cpu — production uses the GPU
# DO uncomment the nvidia deploy.resources block in compose.yaml on the GPU host

curl -fsSL -o fetch_weights.sh \
  https://raw.githubusercontent.com/aalto-speech/dta-server/main/inference/deploy/fetch_weights.sh
chmod +x fetch_weights.sh
./fetch_weights.sh

podman pull ghcr.io/aalto-speech/dta-server:latest
podman pull ghcr.io/aalto-speech/dta-server/inference:latest
systemctl --user stop dta-compose.service
systemctl --user start dta-compose.service

podman logs -f dta-inference          # "ready: ..." in ~20 s on GPU
podman exec dta-inference python selftest.py
```

Then check `/ping` and `/status` as usual, and score one recording before announcing it.

## Rollback

```bash
podman pull ghcr.io/aalto-speech/dta-server:<previous-release-tag>
podman pull ghcr.io/aalto-speech/dta-server/inference:<previous-release-tag>
echo 'DTA_TAG=<previous-release-tag>' >> ~/.config/dta/env   # remove again when rolling forward
cp compose.yaml.bak compose.yaml     # only if compose.yaml itself changed
systemctl --user restart dta-compose.service
```

The weights volume does not need rolling back unless the checkpoint itself changed. If it did,
the startup hash guard will refuse to serve rather than run a mismatched pair — which is the
signal to re-run `fetch_weights.sh` at the matching revision.
