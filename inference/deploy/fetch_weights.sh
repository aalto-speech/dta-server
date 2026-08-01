#!/usr/bin/env bash
# Populate the `asa-weights` podman volume from HuggingFace.
# Run on the deployment server (staging and production alike), once per model version.
#
#   ./fetch_weights.sh                          # defaults to Usin2705/dta_asa
#   ./fetch_weights.sh <other-repo-id>
#
# The repo is PUBLIC, so no credentials are needed. HF_TOKEN is still honoured if set, so this
# keeps working unchanged if the repo is ever made private.
#
# Mirrors setup.sh's create_volumes(): a named volume filled by a throwaway container, so
# nothing needs Python or the HF CLI installed on the host.
#
# ~15.9 GB. Resumable — re-run after an interruption and completed files are skipped.
set -euo pipefail

REPO="${1:-${ASA_WEIGHTS_REPO:-Usin2705/dta_asa}}"
VOLUME="${ASA_WEIGHTS_VOLUME:-asa-weights}"
# Pin to a commit for reproducible deployments: a retrain pushes a new revision, and 'main'
# would silently change what production loads on the next fetch. The startup hash guard would
# catch the resulting image/weights mismatch, but a refusal to boot is a worse way to find out
# than simply not moving.
REVISION="${ASA_WEIGHTS_REVISION:-main}"

echo "Creating volume '${VOLUME}' if absent..."
podman volume create --ignore "${VOLUME}" >/dev/null
MOUNT="$(podman volume inspect -f '{{.Mountpoint}}' "${VOLUME}")"
echo "  -> ${MOUNT}"

# Passed through the environment, never on the command line: podman inspect and the host
# process list would otherwise expose it. Empty is fine for a public repo.
TOKEN_ARGS=()
if [[ -n "${HF_TOKEN:-}" ]]; then
  TOKEN_ARGS=(-e HF_TOKEN)
  echo "Using HF_TOKEN from the environment."
fi

echo "Downloading ${REPO}@${REVISION} into the volume (~15.9 GB)..."
podman run --rm \
  "${TOKEN_ARGS[@]}" \
  -e HF_HUB_ENABLE_HF_TRANSFER=1 \
  -v "${VOLUME}:/weights" \
  docker.io/python:3.12-slim \
  bash -c "pip install --quiet --no-cache-dir 'huggingface_hub[hf_transfer]>=0.34' && \
           python -c \"
from huggingface_hub import snapshot_download
snapshot_download(repo_id='${REPO}', revision='${REVISION}', local_dir='/weights',
                  max_workers=8)
print('download complete')
\""

echo
echo "Contents of ${VOLUME}:"
du -sh "${MOUNT}"/* 2>/dev/null || true
echo
echo "Now restart the stack so the inference container picks them up:"
echo "  systemctl --user restart dta-compose.service"
echo
echo "The container verifies on startup that these weights match the image's assets"
echo "(sha256 of the checkpoint's first 64 MB) and refuses to serve if they do not."
