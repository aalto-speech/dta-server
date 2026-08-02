# Weights: where they come from and how to update them

Weights live in the **`asa-weights` podman volume**, never in the container image. ~15.9 GB
that change only on retrain, against an image that rebuilds on every code change.

Source: **https://huggingface.co/Usin2705/dta_asa** (public — no credentials needed).

```
scorer/model.safetensors        8.24 GB   trained M-CASA checkpoint (complete state dict)
whisper_finnish_v3/             3.06 GB   Finnish ASR fine-tune, also the acoustic backbone
qwen_base/                      4.55 GB   Qwen3.5-2B base (Apache-2.0)
assets/                          small    task catalogue, calibrator, model card
```

## First install, or after a retrain

```bash
./deploy/fetch_weights.sh                 # defaults to Usin2705/dta_asa
systemctl --user restart dta-compose.service
podman logs -f dta-inference              # wait for "ready: finnish-v3_..._s2022"
```

Resumable — re-run after an interruption and completed files are skipped.

## Pin a revision in production

`fetch_weights.sh` defaults to `main`. A retrain pushes a new revision there, so the next
routine fetch would silently change what production loads. Pin it:

```bash
ASA_WEIGHTS_REVISION=<commit-sha> ./deploy/fetch_weights.sh
```

## The image and the weights are updated separately — and must match

`podman pull` updates code **and `assets/`**; `fetch_weights.sh` updates the weights. Do one
without the other and the service would otherwise start happily with, say, a new calibrator or
a renumbered task catalogue applied to an old checkpoint — scoring every recording plausibly
and wrongly, with nothing in any log.

**The container refuses to start when they disagree.** At startup it hashes the first 64 MB of
`scorer/model.safetensors` and compares it against `weights.sha256_first_64mb` in the image's
`assets/model_card.json` (~0.2 s). Tested against a different seed of the same training config
— the closest realistic mistake — and it fires.

```
FAIL  weights and assets present  WEIGHTS DO NOT MATCH THIS IMAGE.
        mounted  /weights/scorer/model.safetensors
          sha256(first 64MB) f9b3322b5b2686bf3a37e4716dbfcbc4...
        expected by assets/model_card.json (finnish-v3_..._s2022)
          sha256(first 64MB) 3996d0ee7ba1889ec4d0fc801f533a41...
```

Fix by refreshing whichever side is stale. `DTA_SKIP_WEIGHT_CHECK=1` bypasses it — only
meaningful while deliberately testing a new checkpoint against old assets.

## Publishing a new checkpoint

On the research machine (`ASA-LLM`):

```bash
# 1. regenerate assets for the new run (edit CHECKPOINT at the top first)
python scripts/dta_production/export_assets.py

# 2. re-verify the input path is still byte-identical to training
python scripts/dta_production/test_input_parity.py

# 3. re-stage the weights folder
python scripts/dta_production/stage_weights.py

# 4. confirm the served model reproduces the checkpoint (GPU)
sbatch scripts/dta_production/verify_parity.sh

# 5. publish
hf auth login
python scripts/dta_production/push_weights_to_hf.py --repo Usin2705/dta_asa \
    --i-understand-public
```

Step 4 before step 5, always: publishing is the irreversible half.

A re-push leaves a hand-edited README on the Hub alone (`--force-readme` to replace it), and
`assets/` is uploaded alongside the weights so the Hub copy always describes the checkpoint
sitting next to it.

Then release `dta-server`, which builds the matching image, and run `fetch_weights.sh` on each
box. The hash guard is what catches you if only one of those two happens.
