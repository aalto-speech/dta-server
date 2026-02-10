#!/usr/bin/env bash
set -euo pipefail

VERBOSE="${VERBOSE:-1}"
DRY_RUN="${DRY_RUN:-0}"

usage () {
  cat <<'EOF'
Usage: [ENV VARS] ./setup.sh [OPTIONS]

Options:
  -q, --quiet     Disable progress logs
  -n, --dry-run   Show what would be executed without running commands
  -h, --help      Show this help

Environment overrides:
  Application:
    APP_USER=saysuomi                             Non-root user to run app and manage resources
    APP_ROOT=/home/${APP_USER}/app                Root directory for application files
    APP_REPO=aalto-speech/dta-server.git          GitHub repository for fetching configuration files
    APP_REF=main                                  Git reference (branch, tag, or commit)
    APP_OWNER=                                    GitHub repository owner for configuration files

  Model:
    MODEL_REPO=Usin2705/CaptainA_v0               LLM repository
    MODEL_REVISION=main                           LLM revision (branch, tag, or commit)
    MODEL_DIR_NAME=CaptainA_v0                    LLM directory name in volume (/hf/models/${MODEL_DIR_NAME})
    HF_MODELS_VOLUME=hf-models                    Podman volume name for downloaded models
    HF_CACHE_VOLUME=hf-cache                      Podman volume name for Hugging Face cache
    HF_TOKEN=                                     Hugging Face API token (for private models)

  Build:
    COMPOSE_FILE=container-compose.yaml           Path to the container compose file
    GITHUB_TOKEN=                                 GitHub token (for private repository access)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -q|--quiet)
      VERBOSE=0
      shift
      ;;
    -n|--dry-run)
      DRY_RUN=1
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

log () { [[ "${VERBOSE}" == "1" ]] && printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

run_cmd () {
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[DRY-RUN] Would execute: $*"
    return 0
  else
    "$@"
  fi
}

# Default environment variables
# Model settings
MODEL_REPO="${MODEL_REPO:-Usin2705/CaptainA_v0}"  # LLM repository
MODEL_REVISION="${MODEL_REVISION:-main}"  # LLM revision (branch, tag, or commit)
MODEL_DIR_NAME="${MODEL_DIR_NAME:-CaptainA_v0}" # LLM directory name the model will be stored under in the volume (e.g., /hf/models/${MODEL_DIR_NAME})
HF_MODELS_VOLUME="${HF_MODELS_VOLUME:-hf-models}" # Podman volume name for storing downloaded models
HF_CACHE_VOLUME="${HF_CACHE_VOLUME:-hf-cache}"  # Podman volume name for storing Hugging Face cache

# Application settings
APP_USER="${APP_USER:-saysuomi}"  # Non-root user to run the application and manage resources
APP_OWNER=  # GitHub repository owner for fetching configuration files
APP_REPO="${APP_REPO:-aalto-speech/dta-server.git}" # GitHub repository for fetching configuration files
APP_REF="${APP_REF:-main}"  # Git reference (branch, tag, or commit)
APP_ROOT="/home/${APP_USER}/app"  # Root directory for application files

# Misc. settings
COMPOSE_FILE=${COMPOSE_FILE:-container-compose.yaml}  # Path to the container compose file


setup_dependencies () {
  # Environment & dependency setup
  log "Setting up environment and dependencies..."
  run_cmd sudo apt update -y
  run_cmd sudo apt install -y podman podman-compose git iptables-persistent

  # ? Service enabling for podman probably not needed
  # # Podman is daemonless, but these improve convenience on some distros
  # log "Enabling podman services..."
  # sudo systemctl enable --now podman-restart.service 2>/dev/null || true
  # sudo systemctl enable --now podman.socket 2>/dev/null || true
}


setup_user () {
  # Create a non-root user for running the application and managing resources

  if id -u "${APP_USER}" >/dev/null 2>&1; then
    log "User '${APP_USER}' already exists. Skipping user creation."
  else
    log "Creating user '${APP_USER}'..."
    run_cmd sudo useradd -m -s /bin/bash "${APP_USER}"
    log "User '${APP_USER}' created successfully."
  fi
}


setup_app_dirs () {
  # Create application directory structure under the user

  log "Creating app user directories if not exist at ${APP_ROOT}..."
  run_cmd sudo mkdir -p \
    "${APP_ROOT}/logs" \
    "${APP_ROOT}/models" \
    "${APP_ROOT}/cache" \
    "${APP_ROOT}/data"
  run_cmd sudo chown -R "${APP_USER}:${APP_USER}" "${APP_ROOT}"
}


setup_networking () {
  # Setup iptables rules to redirect ports 80 and 443 to 8080 and 8443 respectively
  # # This is needed since binding to privileged ports (<1024) typically requires root permissions, which is not ideal for containerized applications. By redirecting to higher ports, the application can run without elevated privileges while still being accessible on standard HTTP/HTTPS ports.

  log "Setting up iptables rules for port redirection..."

  # Redirect port 80 to 8080
  run_cmd sudo iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80 -j REDIRECT --to-ports 8080
  run_cmd sudo iptables -t nat -A OUTPUT -o lo -p tcp --dport 80 -j REDIRECT --to-ports 8080
  run_cmd sudo iptables -A INPUT -i eth0 -p tcp --dport 8080 -j ACCEPT

  # Redirect port 443 to 8443
  run_cmd sudo iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 443 -j REDIRECT --to-ports 8443
  run_cmd sudo iptables -t nat -A OUTPUT -o lo -p tcp --dport 443 -j REDIRECT --to-ports 8443
  run_cmd sudo iptables -A INPUT -i eth0 -p tcp --dport 8443 -j ACCEPT

  log "Saving iptables rules to '/etc/iptables/rules.v[4|6]'..."
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[DRY-RUN] Would execute: sudo iptables-save | sudo tee /etc/iptables/rules.v4"
    log "[DRY-RUN] Would execute: sudo ip6tables-save | sudo tee /etc/iptables/rules.v6"
  else
    sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null
    sudo ip6tables-save | sudo tee /etc/iptables/rules.v6 >/dev/null
  fi
}


setup_model () {
  # Downloads the specified LLM into podman volumes for persistent storage and caching

  log "Creating volumes '${HF_MODELS_VOLUME}' and '${HF_CACHE_VOLUME}' if they do not exist..."
  run_cmd podman volume create --ignore "${HF_MODELS_VOLUME}" >/dev/null
  run_cmd podman volume create --ignore "${HF_CACHE_VOLUME}" >/dev/null

  # Run a temporary container to download the model and cache into the volumes
  log "Downloading model '${MODEL_REPO}' (revision: '${MODEL_REVISION}') into volume '${HF_MODELS_VOLUME}'..."
  local image_name="docker.io/python:3.14-slim"

  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[DRY-RUN] Would execute: podman run (model download)..."
  else
    podman run --rm --pull=always \
    --userns=keep-id \
    -e HF_HOME=/hf \
    -e HUGGINGFACE_HUB_CACHE=/hf/cache/hub \
    -e TRANSFORMERS_CACHE=/hf/cache/transformers \
    -e HF_TOKEN \
    -v "${HF_MODELS_VOLUME}":/hf/models:Z \
    -v "${HF_CACHE_VOLUME}":/hf/cache:Z \
    "${image_name}" \
    bash -lc \
    "pip install -U 'huggingface_hub[cli]' >/dev/null \
      && huggingface-cli download '${MODEL_REPO}' \
        --revision '${MODEL_REVISION}' \
        --local-dir '/hf/models/${MODEL_DIR_NAME}' \
        --local-dir-use-symlinks False"
  fi

  log "Cleaning up temporary containers and images '${image_name}'..."
  if [[ "${DRY_RUN}" != "1" ]]; then
    podman rm -f -i "${image_name}" >/dev/null || true
    podman rmi -f -i "${image_name}" >/dev/null || true
  fi

  log "Model downloaded to volume: ${HF_MODELS_VOLUME}"
  log "Cache stored in volume: ${HF_CACHE_VOLUME}"
}


fetch_file () {
  local path="$1"
  local out="$2"
  curl -fsSL \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.raw" \
    "https://api.github.com/repos/${APP_OWNER}/${APP_REPO}/contents/${path}?ref=${APP_REF}" \
    -o "${out}"
}


fetch_configuration_files () {
  # Fetch configuration files from the application repository to the server

  log "NOT IMPLEMENTED RETURNING 0: Fetching configuration files from ${APP_REPO}..."
  return 0

  local conf_dir="$(mkdir -p ./dta-conf.d)"
  cd "${conf_dir}"

  # # Fetch configuration files using GitHub API (requires GITHUB_TOKEN with repo access)
  # fetch_file "Caddyfile" "Caddyfile"
  # fetch_file "container-compose.yaml" "container-compose.yaml"

  # Fetch configuration files from the application repository
  git init >/dev/null
  git remote add origin https://github.com/${APP_REPO} >/dev/null
  git fetch --depth 1 origin "${APP_REF}" >/dev/null
  git show "${APP_REF}":Caddyfile > ./Caddyfile
  git show "${APP_REF}":container-compose.yaml > ./container-compose.yaml

  # Cleanup git metadata
  rm -rf .git

  cd -
}


setup_app () {
  log "Building application from compose file: ${COMPOSE_FILE}"
  run_cmd podman compose build -f "${COMPOSE_FILE}" --pull --quiet
  log "Run the application with: podman compose -f ${COMPOSE_FILE} up -d"
}


cleanup () {
  if [[ -n "${APP_SRC_TEMP_DIR:-}" && -d "${APP_SRC_TEMP_DIR}" ]]; then
    rm -rf "${APP_SRC_TEMP_DIR}"
  fi
}


main () {
  local start_time=$(date +%s%N)

  trap cleanup EXIT

  if [[ "${DRY_RUN}" == "1" ]]; then
    log "Starting setup in DRY-RUN mode (no commands will be executed)..."
  else
    log "Starting setup..."
  fi

  setup_dependencies

  setup_user
  setup_app_dirs
  setup_networking

  setup_model
  # fetch_configuration_files # TODO: This should be used instead of fetch_app once the configuration files are in place and the application image can be built in CI/CD. See fetch_configuration_files comments.
  setup_app

  local end_time=$(date +%s%N)
  local time_ns=$((end_time - start_time))
  local time_ms=$((time_ns / 1000000))

  log "Model volume: ${HF_MODELS_VOLUME} (model at /hf/models/${MODEL_DIR_NAME} when mounted)"
  log "HF cache volume: ${HF_CACHE_VOLUME}"

  log "Script completed in [${time_ms}ms]"
}

main
