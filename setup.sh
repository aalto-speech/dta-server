#!/usr/bin/env bash
set -euo pipefail

VERBOSE="${VERBOSE:-0}"

usage () {
  cat <<'EOF'
Usage: [ENV VARS] ./setup.sh [OPTIONS]

Options:
  -v, --verbose   Print progress logs
  -h, --help      Show this help

Environment overrides:
  VERBOSE=1
  PROJECT_NAME=saysuomi
  MODEL_REPO=Usin2705/CaptainA_v0
  MODEL_REVISION=main
  MODEL_DIR_NAME=CaptainA_v0
  HF_MODELS_VOLUME=hf-models
  HF_CACHE_VOLUME=hf-cache
  HF_TOKEN=
  APP_REPO=aalto-speech/dta-server.git
  APP_REF=main
  LOCAL_APP_IMAGE_NAME=dta-server:local
  COMPOSE_FILE=container-compose.yaml
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -v|--verbose)
      VERBOSE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log() { [[ "${VERBOSE}" == "1" ]] && printf '%s\n' "$*"; }

# Default environment variables
# Project settings
PROJECT_NAME="${PROJECT_NAME:-saysuomi}"

# Model settings
MODEL_REPO="${MODEL_REPO:-Usin2705/CaptainA_v0}"
MODEL_REVISION="${MODEL_REVISION:-main}"
MODEL_DIR_NAME="${MODEL_DIR_NAME:-CaptainA_v0}"
HF_MODELS_VOLUME="${HF_MODELS_VOLUME:-hf-models}"
HF_CACHE_VOLUME="${HF_CACHE_VOLUME:-hf-cache}"

# Application settings
APP_REPO="${APP_REPO:-aalto-speech/dta-server.git}"
APP_REF="${APP_REF:-main}"
LOCAL_APP_IMAGE_NAME="${LOCAL_APP_IMAGE_NAME:-dta-server:local}"

# Misc. settings
COMPOSE_FILE=${COMPOSE_FILE:-container-compose.yaml}


setup_dependencies () {
  # Environment & dependency setup
  log "Setting up environment and dependencies..."
  sudo dnf update -y
  sudo dnf install -y podman git

  # ? Service enabling for podman probably not needed
  # # Podman is daemonless, but these improve convenience on some distros
  # log "Enabling podman services..."
  # sudo systemctl enable --now podman-restart.service 2>/dev/null || true
  # sudo systemctl enable --now podman.socket 2>/dev/null || true
}


setup_model () {
  # Downloads the specified LLM into podman volumes for persistent storage and caching

  # Create the podman volumes if they do not exist
  # Creating the cache volume helps speed up future downloads of models instead of re-downloading everything. This also allows the pruning of the cache volume without affecting the models.
  log "Creating volumes (for models and cache) if they do not exist..."

  podman volume inspect "${HF_MODELS_VOLUME}" >/dev/null 2>&1 || podman volume create "${HF_MODELS_VOLUME}" >/dev/null
  podman volume inspect "${HF_CACHE_VOLUME}" >/dev/null 2>&1 || podman volume create "${HF_CACHE_VOLUME}" >/dev/null

  # Run a temporary container to download the model and cache into the volumes
  log "Downloading model '${MODEL_REPO}' (revision: '${MODEL_REVISION}') into volume '${HF_MODELS_VOLUME}'..."

  podman run --rm --pull=always \
    --userns=keep-id \
    -e HF_HOME=/hf \
    -e HUGGINGFACE_HUB_CACHE=/hf/cache/hub \
    -e TRANSFORMERS_CACHE=/hf/cache/transformers \
    -e HF_TOKEN \
    -v "${HF_MODELS_VOLUME}":/hf/models:Z \
    -v "${HF_CACHE_VOLUME}":/hf/cache:Z \
    docker.io/python:3.14-slim \
    bash -lc \
    "pip install -U 'huggingface_hub[cli]' >/dev/null \
      && huggingface-cli download '${MODEL_REPO}' \
        --revision '${MODEL_REVISION}' \
        --local-dir '/hf/models/${MODEL_DIR_NAME}' \
        --local-dir-use-symlinks False"

  log "Model downloaded to volume: ${HF_MODELS_VOLUME}"
  log "Cache stored in volume: ${HF_CACHE_VOLUME}"
  # echo "Example mount into an inference container: -v ${HF_MODELS_VOLUME}:/hf/models:Z"
}


fetch_app () {
  # Fetch application source code for building the container image locally
  # ? Maybe fetching not needed before the application image can be built in CI/CD. Instead, copy repository from developer machine to server `scp -r ./dta-server user@host:~/app`
  # ! This is a temporary mechanism; in production, the build should happen in a CI/CD pipeline, thus eliminating the need for this function.

  log "Fetching application source code for building the container image..."

  # Temporary fetch: clone to a temp dir, build from there, then remove.

  if [[ -z "${APP_REPO}" ]]; then
    # If this script is already inside a git checkout, use it as the build context.
    local script_dir
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}" )" && pwd)"
    if [[ -d "${script_dir}/.git" ]]; then
      APP_SRC_DIR="${script_dir}"
      APP_SRC_TEMP_DIR=""
      log "Using local repo as build context: ${APP_SRC_DIR}"
      return 0
    fi

    echo "APP_REPO is not set and script is not in a git repo." >&2
    echo "Set APP_REPO=<user>/<repo>.git to enable temporary fetch." >&2
    return 1
  fi

  APP_SRC_TEMP_DIR="$(mktemp -d)"
  APP_SRC_DIR="${APP_SRC_TEMP_DIR}/app"
  log "Cloning ${APP_REPO} (ref ${APP_REF}) into ${APP_SRC_DIR}"
  git clone --depth 1 --branch "${APP_REF}" "https://github.com/${APP_REPO}" "${APP_SRC_DIR}" >/dev/null
}


fetch_configuration_files () {
  # Fetch configuration files from the application repository to the server
  # TODO: Fetch configuration files from the application repository to server
  log "Fetching container-compose.yaml from ${APP_REPO}... (not implemented) Returning 0..."
  return 0

  # Fetch container-compose.yaml and Caddyfile from the application repository to a temp dir, so podman compose can use it
  COMPOSE_TEMP_DIR="$(mktemp -d)"
  cd ${COMPOSE_TEMP_DIR}
  git init >/dev/null
  git remote add origin git@github.com:${APP_REPO} >/dev/null
  git fetch --depth 1 origin HEAD >/dev/null
  git show HEAD:container-compose.yaml > container-compose.yaml
  git show HEAD:Caddyfile > Caddyfile
  cd -
  # podman compose -f ${COMPOSE_TEMP_DIR}/container-compose.yaml up -d --build
}


setup_app () {
  # See fetch_app for details
  # fetch_app # TODO: Uncomment if needed. See fetch_app comments.

  log "Building application image: ${LOCAL_APP_IMAGE_NAME}"
  # ! Temporary build from source code until container-compose.yaml is available in the repo
  podman build -t "${LOCAL_APP_IMAGE_NAME}" "${APP_SRC_DIR}"
  log "Application image built: ${LOCAL_APP_IMAGE_NAME}"

  # TODO: Podman compose to run Caddy, application and database.
  # TODO: Get container-compose.yaml from the application repository.

  # NB: Setup should not start the application automatically.
}


cleanup () {
  if [[ -n "${APP_SRC_TEMP_DIR:-}" && -d "${APP_SRC_TEMP_DIR}" ]]; then
    rm -rf "${APP_SRC_TEMP_DIR}"
  fi
}


main() {
  trap cleanup EXIT

  setup_dependencies
  setup_model
  setup_app

  echo "Done."
  echo "Model volume: ${HF_MODELS_VOLUME} (model at /hf/models/${MODEL_DIR_NAME} when mounted)"
  echo "HF cache volume: ${HF_CACHE_VOLUME}"
  echo "App image: ${LOCAL_APP_IMAGE_NAME:-dta-server:local}"
}

main
