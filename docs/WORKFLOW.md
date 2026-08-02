# Development → Staging → Production workflow

This guide explains how a code change travels from your editor to the production
server, step by step, with the exact commands. It assumes no prior CI/CD knowledge.

## The big picture

```
 you                        GitHub                         servers
 ───────────────────────    ─────────────────────────      ──────────────────────────
 edit code
   │
   ├─ push to `dev` ──────► CI: lint + tests
   │                          └─ CD: builds container images
   │                             tagged  :staging
   │                                        │
   │                                        ▼
   │                        ghcr.io/aalto-speech/dta-server:staging
   │                        ghcr.io/aalto-speech/dta-server/inference:staging
   │                                        │
   │                          STAGING box pulls :staging, restarts, you test
   │
   ├─ PR `dev` → `main`, merge
   │
   └─ publish a GitHub Release (e.g. v1.1.0) ──► CI + CD again, now tagged
                                                 :latest and :v1.1.0
                                                     │
                              PRODUCTION box pulls :latest, restarts, you verify
```

Key ideas:

- **Nothing deploys automatically to a server.** GitHub only *builds and stores*
  container images (in the GitHub Container Registry, "ghcr"). A human always logs
  into the server and pulls the new image. This is deliberate: a bad change can
  never reach production without someone deciding to pull it.
- **`dev` is the staging branch.** Every push to `dev` produces fresh `:staging`
  images. Staging servers run `:staging`.
- **`main` + a Release is production.** Merging to `main` alone builds nothing —
  you must *publish a Release* on GitHub, which builds `:latest` plus a
  version-numbered tag (e.g. `:v1.1.0`). Production servers run `:latest`; the
  version tags exist so you can roll back.
- **Two images move together.** The app (`dta-server`) and the speech scorer
  (`dta-server/inference`) are always built with the same tag from the same
  commit, so the pair is consistent. (CD skips rebuilding the ~10 GB inference
  image on pushes that didn't touch `inference/`, but releases always build both.)
- **Which image a server runs is chosen by `DTA_TAG`** in `~/.config/dta/env` on
  that server: `DTA_TAG=staging` on staging, unset (= `latest`) on production.

## The production GPU is Pascal

> [!WARNING]
> Production runs a **Tesla P100 (Pascal, compute capability sm_60)**. Modern torch
> wheels do not support it: **every torch ≥ 2.8 wheel (and every cu128 wheel) ships
> sm_61+ kernels only** and dies on the P100 with "no kernel image is available for
> execution on the device". This is why v1.1.0 could never be deployed and v1.1.1
> exists. Staging has no GPU (`DTA_DEVICE=cpu`), so **CPU-green on staging proves
> nothing about the GPU** — the P100 on production is the only place the GPU path runs.

Files that are load-bearing for the GPU. Changing any of them requires re-verifying on
the production P100 (one-shot `selftest.py` against the candidate image, see below)
**before** publishing a release:

| File | What must hold |
| --- | --- |
| `inference/Containerfile` | `TORCH_SPEC`/`TORCH_INDEX` args stay at `torch==2.7.1` + cu126 — the last build with sm_60 kernels — until the GPU itself is upgraded (then restore 2.9.1/cu128) |
| `inference/requirements.txt` | the torch line stays a floor (`>=2.4`), never a hard pin — a pin re-upgrades torch from PyPI over the CUDA-matched install |
| `inference/dta_scorer/config.py` | `DTA_AUTOCAST_DTYPE` keeps supporting `float16` — production sets it (Pascal has no bf16; fp32 halves throughput) |
| `compose.yaml` | the inference service keeps the `DTA_AUTOCAST_DTYPE` pass-through |

The verified numbers to compare against (P100, fp16, 2026-08-02): selftest PASS, model
load ~25 s, 8 s recording scored in ~1.7 s, calibrated CEFR identical to the CPU
(fp32) path, raw deviations ≤ 0.01.

## Day-to-day: shipping a change to staging

```bash
# on your machine, in the dta-server checkout
git switch dev
git pull
# ... edit code ...
git add -A && git commit -m "describe the change"
git push origin dev
```

Watch the build at <https://github.com/aalto-speech/dta-server/actions> (a push
that changes `inference/` takes ~10–15 min because of the large CUDA image;
app-only pushes are much faster). When it's green:

```bash
# on the STAGING server
ssh -i ~/.ssh/keyname.pem ubuntu@<staging-floating-ip>
podman pull ghcr.io/aalto-speech/dta-server:staging
podman pull ghcr.io/aalto-speech/dta-server/inference:staging   # only if inference/ changed
systemctl --user restart dta-compose.service
podman ps                        # all three containers up?
podman logs -f dta               # watch the app come up
```

Then test what you changed (see the smoke tests below).

> [!NOTE]
> If you changed `compose.yaml`, `Caddyfile`, or `dta-compose.service`, those are
> **not** inside any image — re-fetch them on the server first, see
> [UPDATING.md](./UPDATING.md).

## Releasing to production

1. Open a pull request from `dev` to `main` on GitHub, review it, merge it.
2. On GitHub: **Releases → Draft a new release → Choose a tag** → type a new
   version (e.g. `v1.1.0`), target `main`, write a short changelog → **Publish**.
   This triggers the build of `:latest` + `:v1.1.0` for **both** images.
3. When Actions is green, deploy on the production server:

```bash
ssh -i ~/.ssh/keyname.pem ubuntu@<production-floating-ip>
podman pull ghcr.io/aalto-speech/dta-server:latest
podman pull ghcr.io/aalto-speech/dta-server/inference:latest

# selftest BEFORE starting the stack — it loads its own ~10 GB pipeline, and the
# 16 GB P100 cannot hold two copies (exec-ing it next to the live server OOMs):
podman run --rm --device nvidia.com/gpu=all -v asa-weights:/weights:ro \
  -e DTA_AUTOCAST_DTYPE=float16 \
  ghcr.io/aalto-speech/dta-server/inference:latest python selftest.py   # must end with PASS

systemctl --user restart dta-compose.service
podman logs -f dta-inference     # wait for "ready: finnish-v3_..." (~30 s on GPU)
podman exec dta-inference curl -s http://localhost:8000/health          # "device":"cuda"
```

4. Smoke-test before announcing:

```bash
curl -s https://<production-domain>/api/v1/ping
# score one real recording end-to-end (valid user guid + task_id 1-5):
curl -s -F "file=@test.wav" -F "guid=<uuid>" -F "task_id=1" \
     https://<production-domain>/api/v1/speech/assess
```

## Rolling back production

Every release keeps its version-numbered images, so rollback is a pull of the
previous version:

```bash
# on the production server
podman pull ghcr.io/aalto-speech/dta-server:v1.0.0            # <- previous good version
podman pull ghcr.io/aalto-speech/dta-server/inference:v1.0.0
```

Then point the stack at that tag by setting `DTA_TAG=v1.0.0` in
`~/.config/dta/env` and `systemctl --user restart dta-compose.service`.
(Remove the line again after rolling forward.) If `compose.yaml` itself changed,
restore your backup copy (`cp compose.yaml.bak compose.yaml`) too.

> [!WARNING]
> **`v1.1.0` is not a valid rollback target on production**: its inference image is the
> torch 2.9.1/cu128 build, which cannot run on the P100 (see the Pascal section above).
> The floor for working GPU inference is `v1.1.1`; `v1.0.0` "works" but is the whisper
> stub that returns random scores.

The model weights volume does **not** need to roll back unless the checkpoint
changed; the inference container refuses to start on a genuine image/weights
mismatch, which is your signal.

## First-time deployment of the ASA scorer (one-off migration notes)

The M-CASA scorer (added 2026-08) changed two things existing servers must
absorb once:

1. **Weights volume.** ~15.9 GB of model weights live in the `asa-weights`
   podman volume, not in any image. On each server, once:

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/aalto-speech/dta-server/refs/heads/main/inference/deploy/fetch_weights.sh
   chmod +x fetch_weights.sh
   # pin the exact weights revision (repeatable deploys):
   ASA_WEIGHTS_REVISION=86707fe388ac0c43e35b184e95e624e0841cb2f4 ./fetch_weights.sh
   ```

2. **Score scale is now CEFR 0–6** (0 = below A1 … 6 = C2; the served model
   caps at 3.5 = B1+). The database CHECK constraints were widened from 0–5 to
   0–6, but SQLite cannot alter constraints on an existing database, and old
   databases only contain random placeholder scores from the stub scorer.
   **Delete the old database once** so the new schema is created fresh
   (`/data/dta.db*` inside the `database` volume — see UPDATING.md), or keep the
   old file if you accept the 0–5 constraint (safe today: the model never
   emits > 3.5, but new placeholder-free data is cleaner).

3. **Device.** Production (GPU): keep `DTA_DEVICE` unset/`cuda`, set
   `DTA_AUTOCAST_DTYPE=float16` (P100 — see the Pascal section above), and give
   the inference service the GPU via the CDI form in `~/dta/compose.yaml`
   (`devices: [nvidia.com/gpu=all]`; the commented `deploy.resources` block is
   unreliable under podman-compose — see [SETUP.md](./SETUP.md#gpu-runtime-production-only)).
   Staging (no GPU): set `DTA_DEVICE=cpu` and `ASA_TIMEOUT=300` in
   `~/.config/dta/env` — model load takes ~10 min and each scoring call 30–60 s
   there; that is expected.

## GitHub Container Registry (ghcr) access

The `aalto-speech` organization **disables public packages**, so both images are
private and every server must authenticate to ghcr once before it can pull:

```bash
gh auth login                      # or use a PAT with read:packages
gh auth token | podman login ghcr.io -u <github-username> --password-stdin
```

Podman writes the login to `${XDG_RUNTIME_DIR}/containers/auth.json`, which is
a tmpfs and **does not survive a reboot**. Persist it once:

```bash
mkdir -p ~/.config/containers
cp /run/user/$(id -u)/containers/auth.json ~/.config/containers/auth.json
chmod 600 ~/.config/containers/auth.json
```

(podman also reads `~/.config/containers/auth.json`.) If pulls start failing
with `unauthorized`, the token expired — re-run the login commands above.

## Cheat sheet

| I want to…                       | Do this                                                            |
| -------------------------------- | ------------------------------------------------------------------ |
| Test a change safely             | push to `dev`, pull `:staging` on the staging box                  |
| Ship to production               | PR `dev`→`main`, publish a Release, pull `:latest` on prod         |
| Roll back production             | set `DTA_TAG=<previous version>` in env, pull those tags, restart  |
| See build status                 | <https://github.com/aalto-speech/dta-server/actions>               |
| Update server config files       | [UPDATING.md](./UPDATING.md)                                       |
| Provision a brand-new server     | [SETUP.md](./SETUP.md) (+ [POUTA.md](./POUTA.md) for the VM)       |
| Understand the scorer            | [../inference/README.md](../inference/README.md)                   |
| Find / download the data         | [DATA.md](./DATA.md)                                               |
| Change torch / inference deps    | Read [the Pascal section](#the-production-gpu-is-pascal) first     |
