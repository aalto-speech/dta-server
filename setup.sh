#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="${0}"

VERBOSE="${VERBOSE:-1}"
DRY_RUN="${DRY_RUN:-0}"
declare -a CONFIG_FILES=()

usage() {
  cat <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS...]

Required options:
  -R, --run            Execute the script. If not provided, the script will only print the usage
                       instructions.

Options:
  -q, --quiet          Disable progress logs (only disables logs from the setup script itself,
                       not from other commands ran within the script)

  -n, --interface <interface>
                       Network interface to apply iptables rules on (default: [auto-detected|eth0])

  -r, --repo <user/repo[:ref]>
                       GitHub repository and optional revision. If no revision given, defaults to main

  -m, --model <user/repo[:rev]>
                       Hugging Face repository and optional revision. If no revision given, defaults to main

  -e, --env <KEY=VALUE>
                       Set or override an environment variable for this run.
                       Can be provided multiple times.

  -h, --help           Show this help

Environment overrides:
  Setup (NOT saved in env file for compose.yaml):
    HF_TOKEN=          Hugging Face API token (for private repositories and increased rate limits)
    GITHUB_TOKEN=      GitHub token (for private repository access and increased rate limits)

  Caddy (saved in env file for compose.yaml):
    ACME_EMAIL=        Email address for ACME registration (optional, for automatic TLS certificates)
    DOMAIN=            Domain name for Caddy configuration (default: localhost)
    UPSTREAM=          Upstream address for Caddy to proxy to (default: dta:8000)

  Application (saved in env file for compose.yaml):
    APP_ENV=           Application environment (default: development)
    DATABASE=          Absolute path to the application database (default: /data/dta.db)
    ADMIN_API_KEY=     Admin API key for the application (default: empty, must be set in production environment)
EOF
}

RUN_MODE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
  -R | --run)
    RUN_MODE=1
    shift
    ;;
  -q | --quiet)
    VERBOSE=0
    shift
    ;;
  -n | --interface)
    NETWORK_INTERFACE="$2"
    shift 2
    ;;
  -r | --repo)
    GITHUB_REPO="${2%%:*}"
    if [[ "$2" == *:* ]]; then
      GITHUB_REPO_REF="${2#*:}"
    fi
    shift 2
    ;;
  -m | --model)
    MODEL_REPO="${2%%:*}"
    if [[ "$2" == *:* ]]; then
      MODEL_REV="${2#*:}"
    fi
    shift 2
    ;;
  -e | --env)
    if [[ -z "${2:-}" || "${2}" != *=* ]]; then
      echo "Error: -e|--env requires KEY=VALUE format" >&2
      exit 2
    fi

    env_key="${2%%=*}"
    env_value="${2#*=}"

    if [[ ! "${env_key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "Error: Invalid environment variable name '${env_key}'" >&2
      exit 2
    fi

    export "${env_key}=${env_value}"
    shift 2
    ;;
  -h | --help)
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

if [[ $RUN_MODE -ne 1 ]]; then
  usage
  exit 0
fi

log() { [[ "${VERBOSE}" == "1" ]] && printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

validate_input() {
  local var_name="$1"
  local var_value="$2"
  local pattern="$3"

  if [[ ! "${var_value}" =~ ${pattern} ]]; then
    echo "Error: Invalid ${var_name}: '${var_value}'" >&2
    echo "Must match pattern: ${pattern}" >&2
    exit 1
  fi
}

validate_path() {
  local path="$1"
  # Prevent path traversal attacks
  if [[ "${path}" == *".."* ]] || [[ "${path}" == /* ]]; then
    echo "Error: Invalid path '${path}' - absolute paths and path traversal not allowed" >&2
    exit 1
  fi
}

# * Environment file variables (saved to env file for compose.yaml)
# Environment variables for Caddy
ACME_EMAIL=${ACME_EMAIL:-}
DOMAIN=${DOMAIN:-localhost}
UPSTREAM=${UPSTREAM:-dta:8000}

# Environment variables for the application
APP_ENV=${APP_ENV:-development}
DATABASE=${DATABASE:-/data/dta.db}
ADMIN_API_KEY=${ADMIN_API_KEY:-}

# * Advanced variables (not saved to env file, only used during setup)
# Environment variables for external services and APIs
HF_TOKEN="${HF_TOKEN:-}"         # Hugging Face API token (optional, for private repositories and increased rate limits)
GITHUB_TOKEN="${GITHUB_TOKEN:-}" # GitHub token (optional, for private repository access and increased rate limits)

# Default environment variables. Use CLI flag to override.
NETWORK_INTERFACE="${NETWORK_INTERFACE:-}"           # Network interface to apply iptables rules on (auto-detected if not set)
CADDY_LOGS_VOLUME="${CADDY_LOGS_VOLUME:-caddy-logs}" # Podman volume name
TARGET_OS_VERSION="${TARGET_OS_VERSION:-24.04}"      # Target Ubuntu version for package selection (e.g., for podman-compose vs docker-compose)

# Model settings
MODEL_REPO="${MODEL_REPO:-}"   # LLM repository
MODEL_REV="${MODEL_REV:-main}" # LLM revision (branch, tag, or commit)

HF_MODELS_VOLUME="${HF_MODELS_VOLUME:-hf-models}" # Podman volume name for storing downloaded models
HF_CACHE_VOLUME="${HF_CACHE_VOLUME:-hf-cache}"    # Podman volume name for storing Hugging Face cache

# Deployment settings
USERNAME="${USERNAME:-ubuntu}"                        # Non-root user to run the application and manage resources
USER_PATH="${USER_PATH:-/home/${USERNAME}}"           # Root directory for the application user (e.g., for logs, data, etc.)
APP_PATH="${USER_PATH}/dta"                           # Directory where the application code and configuration will be stored
GITHUB_REPO="${GITHUB_REPO:-aalto-speech/dta-server}" # GitHub repository for fetching configuration files
GITHUB_REPO_REF="${GITHUB_REPO_REF:-main}"            # GitHub reference (branch, tag, or commit)

# Misc. settings
SERVICE_PATH="dta-compose.service" # Path of the systemd user service file to fetch from the repository and enable

if [[ ${#CONFIG_FILES[@]} -eq 0 ]]; then
  # Default configuration files to fetch from the repository
  CONFIG_FILES=("Caddyfile" "compose.yaml")
fi

# Validate critical inputs to prevent command injection
validate_input "GITHUB_REPO" "${GITHUB_REPO}" '^$|^[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+$'
validate_input "GITHUB_REPO_REF" "${GITHUB_REPO_REF}" '^[a-zA-Z0-9/_.-]+$'
validate_input "MODEL_REPO" "${MODEL_REPO}" '^$|^[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+$'
validate_input "MODEL_REV" "${MODEL_REV}" '^[a-zA-Z0-9/_.-]+$'
validate_input "USERNAME" "${USERNAME}" '^[a-z_][a-z0-9_-]*$'

# Validate config file paths
for config_file in "${CONFIG_FILES[@]}"; do
  validate_path "${config_file}"
done

setup_dependencies() {
  # Install required dependencies based on the OS version

  is_target_version() {
    local current="$1"
    local target="$2"
    [[ "$(printf '%s\n' "${current}" "${target}" | sort -V | head -n1)" == "${target}" ]]
  }

  local current_version=""
  local packages=(
    ca-certificates
    curl
    git
    iptables-persistent
    podman
  )

  current_version="$(lsb_release -rs)"

  if is_target_version "${current_version}" "${TARGET_OS_VERSION}"; then
    packages+=(podman-compose)
  else
    packages+=(docker-compose)
  fi

  log "Detected OS version: '${current_version}'. Target version for package selection: '${TARGET_OS_VERSION}'."
  log "Updating and installing required dependencies..."
  sudo apt update
  sudo apt install -y "${packages[@]}"

  unset -f is_target_version
}

setup_app_dirs() {
  # Create application directory structure under the user

  local dirs=(
    "${APP_PATH}/conf.d"
    "${APP_PATH}/data"
    "${APP_PATH}/logs"
  )

  log "Creating app user directories if they do not exist at '${APP_PATH}'..."
  mkdir -p "${dirs[@]}"

  log "Setting ownership of '${APP_PATH}' to '${USERNAME}'..."
  sudo chown -R "${USERNAME}:${USERNAME}" "${APP_PATH}"

  log "Setting up Caddy logs volume '${CADDY_LOGS_VOLUME}' and symlink to '${APP_PATH}/logs/caddy'..."
  podman volume create --ignore "${CADDY_LOGS_VOLUME}" >/dev/null

  if [[ ! -L "${APP_PATH}/logs/caddy" ]]; then
    ln -s "$(podman volume inspect -f "{{.Mountpoint}}" "${CADDY_LOGS_VOLUME}")" "${APP_PATH}/logs/caddy"
  fi
}

setup_networking() {
  # Setup iptables rules to redirect ports 80 and 443 to 8080 and 8443 respectively

  # * This is needed since binding to privileged ports (<1024) typically requires root permissions, which is not ideal for containerized applications ran through podman. By redirecting to higher ports, the application can run without elevated privileges while still being accessible on standard HTTP/HTTPS ports.

  set -o errtrace

  get_interface() {
    # Auto-detect network interface if not provided, with fallback to 'eth0' and warning if detection fails
    local interface="${NETWORK_INTERFACE:-}"
    if [[ -z "${interface}" ]]; then
      interface=$(ip route | awk '/default/ {print $5; exit}')
      if [[ -z "${interface}" ]]; then
        interface="eth0"
        log "[WARN] Could not auto-detect default network interface, defaulting to 'eth0'." >&2
      else
        log "Auto-detected default network interface: '${interface}'" >&2
      fi
    fi
    echo "${interface}"
  }

  rule_exists() {
    local chain="$1"
    local interface="$2"
    local port="$3"
    local to_port="$4"
    sudo iptables -t nat -C "$chain" -i "$interface" -p tcp --dport "$port" -j REDIRECT --to-ports "$to_port" 2>/dev/null
  }

  backup_iptables() {
    local temp_dir="$1"
    local temp_file=""

    temp_file="$(mktemp -p "${temp_dir}")"
    sudo iptables-save | tee "${temp_file}"

    echo "${temp_file}"
  }

  restore_iptables() {
    trap - ERR INT TERM EXIT
    log "Error occurred, restoring iptables..."
    sudo cat "${iptables4_backup}" | sudo iptables-restore
    sudo cat "${iptables6_backup}" | sudo ip6tables-restore

    if [[ -n "${iptables_backup_dir}" && -d "${iptables_backup_dir}" ]]; then
      rm -rf "${iptables_backup_dir}"
      log "Removed temporary iptables backup directory '${iptables_backup_dir}'."
    fi
    unset iptables_backup_dir iptables4_backup iptables6_backup
  }

  apply_iptables() {
    # Remove existing rules for port 80
    while sudo iptables -t nat -C PREROUTING -i "${interface}" -p tcp --dport 80 -j REDIRECT --to-ports 8080 2>/dev/null; do
      sudo iptables -t nat -D PREROUTING -i "${interface}" -p tcp --dport 80 -j REDIRECT --to-ports 8080
    done
    while sudo iptables -t nat -C OUTPUT -o lo -p tcp --dport 80 -j REDIRECT --to-ports 8080 2>/dev/null; do
      sudo iptables -t nat -D OUTPUT -o lo -p tcp --dport 80 -j REDIRECT --to-ports 8080
    done
    while sudo iptables -C INPUT -i "${interface}" -p tcp --dport 8080 -j ACCEPT 2>/dev/null; do
      sudo iptables -D INPUT -i "${interface}" -p tcp --dport 8080 -j ACCEPT
    done

    # Remove existing rules for port 443
    while sudo iptables -t nat -C PREROUTING -i "${interface}" -p tcp --dport 443 -j REDIRECT --to-ports 8443 2>/dev/null; do
      sudo iptables -t nat -D PREROUTING -i "${interface}" -p tcp --dport 443 -j REDIRECT --to-ports 8443
    done
    while sudo iptables -t nat -C OUTPUT -o lo -p tcp --dport 443 -j REDIRECT --to-ports 8443 2>/dev/null; do
      sudo iptables -t nat -D OUTPUT -o lo -p tcp --dport 443 -j REDIRECT --to-ports 8443
    done
    while sudo iptables -C INPUT -i "${interface}" -p tcp --dport 8443 -j ACCEPT 2>/dev/null; do
      sudo iptables -D INPUT -i "${interface}" -p tcp --dport 8443 -j ACCEPT
    done

    # Add rules for port 80
    sudo iptables -t nat -A PREROUTING -i "${interface}" -p tcp --dport 80 -j REDIRECT --to-ports 8080
    sudo iptables -t nat -A OUTPUT -o lo -p tcp --dport 80 -j REDIRECT --to-ports 8080
    sudo iptables -A INPUT -i "${interface}" -p tcp --dport 8080 -j ACCEPT

    # Add rules for port 443
    sudo iptables -t nat -A PREROUTING -i "${interface}" -p tcp --dport 443 -j REDIRECT --to-ports 8443
    sudo iptables -t nat -A OUTPUT -o lo -p tcp --dport 443 -j REDIRECT --to-ports 8443
    sudo iptables -A INPUT -i "${interface}" -p tcp --dport 8443 -j ACCEPT
  }

  persist_iptables() {
    sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null
    sudo ip6tables-save | sudo tee /etc/iptables/rules.v6 >/dev/null
  }

  cleanup() {
    log "Cleaning up temporary files..."
    trap - ERR INT TERM EXIT

    if [[ -n "${iptables_backup_dir}" && -d "${iptables_backup_dir}" ]]; then
      rm -rf "${iptables_backup_dir}"
      log "Removed temporary iptables backup directory '${iptables_backup_dir}'."
    fi
  }

  local interface=""
  local iptables_backup_dir=""
  local iptables4_backup=""
  local iptables6_backup=""

  log "Getting network interface for iptables rules..."
  interface="$(get_interface)"

  log "Backing up current iptables rules to allow restoration in case of errors..."
  iptables_backup_dir="$(mktemp -d)"
  iptables4_backup="$(backup_iptables "${iptables_backup_dir}")"
  iptables6_backup="$(backup_iptables "${iptables_backup_dir}")"
  unset -f backup_iptables

  trap 'restore_iptables' ERR INT TERM EXIT

  log "Applying iptables rules to redirect ports 80 and 443 to 8080 and 8443 respectively on interface '${interface}'..."
  apply_iptables

  unset -f apply_iptables

  log "Preserving iptables rules across reboots..."
  persist_iptables

  unset -f persist_iptables

  cleanup
  unset -f cleanup
  return 0
}

setup_model() {
  # Downloads the specified LLM into podman volumes for persistent storage and caching

  create_volumes() {
    # Removing any existing 'models' and 'cache' directories to prevent conflicts with the symlinks to the volumes
    rm -rf "${APP_PATH}/models" "${APP_PATH}/cache"

    log "Creating volumes '${HF_MODELS_VOLUME}' and '${HF_CACHE_VOLUME}' if they do not exist..."
    podman volume create --ignore "${HF_MODELS_VOLUME}" >/dev/null
    podman volume create --ignore "${HF_CACHE_VOLUME}" >/dev/null

    log "Creating symlinks for 'model' and 'cache' volumes in '${APP_PATH}'..."
    ln -s "$(podman volume inspect -f "{{.Mountpoint}}" "${HF_MODELS_VOLUME}")" "${APP_PATH}/models"
    ln -s "$(podman volume inspect -f "{{.Mountpoint}}" "${HF_CACHE_VOLUME}")" "${APP_PATH}/cache"
  }

  download_model() {
    # Use a temporary container to download the model and cache into the volumes

    cleanup() {
      log "Cleaning up temporary files..."
      trap - EXIT INT TERM ERR

      if [[ -n "${token_dir}" && -d "${token_dir}" ]]; then
        rm -rf "${token_dir}"
        log "Removed temporary token directory '${token_dir}'."
      fi

      podman rm -f -i "${image_name}" >/dev/null || true
      podman rmi -f -i "${image_name}" >/dev/null || true

      unset image_name token_dir token_file env_args
    }

    local image_name="docker.io/python:3.14-slim"
    local model_name="${MODEL_REPO##*/}"
    local token_dir=""
    local token_file=""
    local env_args=(
      -e HOME=/hf
      -e HF_HOME=/hf
      -e HUGGINGFACE_HUB_CACHE=/hf/cache/hub
      -e TRANSFORMERS_CACHE=/hf/cache/transformers
    )

    if [[ -n "${HF_TOKEN}" ]]; then
      token_dir="$(mktemp -d)"
      chmod 700 "${token_dir}"
      token_file="$(mktemp -p "${token_dir}")"
      echo "HF_TOKEN=${HF_TOKEN}" >"${token_file}"
      chmod 600 "${token_file}"
      env_args+=(--env-file "${token_file}")

      trap 'cleanup' EXIT INT TERM ERR
    fi

    log "Downloading model '${model_name}:${MODEL_REV}' into volume '${HF_MODELS_VOLUME}' using a temporary container..."
    podman run --rm --pull=always \
      "${env_args[@]}" \
      -v "${HF_MODELS_VOLUME}":/hf/models:Z \
      -v "${HF_CACHE_VOLUME}":/hf/cache:Z \
      "${image_name}" \
      bash -lc \
      "mkdir -p ${HOME}/.cache ${HOME}/.local && chmod -R 700 ${HOME} && \
      python -m pip install -U pip >/dev/null && \
      pip install -U huggingface_hub >/dev/null && \
      hf download '${model_name}' \
        --revision '${MODEL_REV}' \
        --local-dir '/hf/models/${model_name}'"

    cleanup
    unset -f cleanup
  }

  create_volumes

  # Setup language model only if MODEL_REPO is provided
  if [[ -n "${MODEL_REPO}" ]]; then
    download_model
    log "Model downloaded to volume '${HF_MODELS_VOLUME}'."
  else
    log "No language model repository provided, skipping model download..."
  fi

  log "Cache stored in volume '${HF_CACHE_VOLUME}'."
}

fetch_file() {
  cleanup() {
    trap - EXIT INT TERM ERR
    if [[ -n "${config_dir}" && -d "${config_dir}" ]]; then
      rm -rf "${config_dir}"
    fi
    unset path out curl_args config_dir config_file
  }

  local path="$1"
  local out="$2"
  local curl_args=(-fsSL -H "Accept: application/vnd.github.raw")
  local config_dir=""
  local config_file=""

  if [[ -n "${GITHUB_TOKEN}" ]]; then
    config_dir="$(mktemp -d)"
    chmod 700 "${config_dir}"
    config_file="$(mktemp -p "${config_dir}")"
    cat >"${config_file}" <<CONFIG_EOF
header = "Authorization: Bearer ${GITHUB_TOKEN}"
CONFIG_EOF
    chmod 600 "${config_file}"
    curl_args+=(--config "${config_file}")

    # Set up trap to ensure cleanup
    trap 'cleanup' EXIT INT TERM ERR
  fi

  curl "${curl_args[@]}" \
    "https://api.github.com/repos/${GITHUB_REPO}/contents/${path}?ref=${GITHUB_REPO_REF}" \
    -o "${out}"

  cleanup
  unset -f cleanup
}

fetch_configuration() {
  # Fetch configuration files from the application repository to the server

  log "Fetching files from repository '${GITHUB_REPO}:${GITHUB_REPO_REF}'..."

  local conf_dir="${APP_PATH}"

  for config_file in "${CONFIG_FILES[@]}"; do
    local out="${conf_dir}/${config_file##*/}"
    log "Fetching file '${config_file}' to '${out}'..."
    fetch_file "${config_file}" "${out}"
  done

  unset conf_dir
}

setup_env_file() {
  local env_path="/home/${USERNAME}/.config/dta"
  local env_file="${env_path}/env"

  if [[ -f "${env_file}" ]]; then
    log "Env file '${env_file}' already exists, skipping creation..."
    return 1
  fi

  mkdir -p "${env_path}"

  chmod 700 "$env_path"
  chown -R "${USERNAME}":"${USERNAME}" "${env_path}"

  log "Creating env file at '${env_file}' with environment variables for the compose..."

  cat >"${env_file}" <<ENV_EOF
# Environment variables for caddy
ACME_EMAIL=${ACME_EMAIL:-}
DOMAIN=${DOMAIN:-}
UPSTREAM=${UPSTREAM:-}

# Environment variables for the application
APP_ENV=${APP_ENV:-}
DATABASE=${DATABASE:-}
AUDIO_SAVE_DIR=${AUDIO_SAVE_DIR:-}
LOGS_SAVE_DIR=${LOGS_SAVE_DIR:-}
LOG_LEVEL=${LOG_LEVEL:-}
ADMIN_API_KEY=${ADMIN_API_KEY:-}
MIN_COHORT_SIZE=${MIN_COHORT_SIZE:-}
MIN_USER_ASSESSMENTS=${MIN_USER_ASSESSMENTS:-}
ENV_EOF
  chmod 600 "${env_file}"
}

enable_services() {
  log "Enabling services to start on boot..."

  local file="${SERVICE_PATH##*/}"
  local path="${USER_PATH}/.config/systemd/user/${file}"
  mkdir -p "$(dirname "${path}")"

  log "Fetching service file '${file}' to '${path}'..."
  fetch_file "${file}" "${path}"

  log "Enabling lingering for user '${USERNAME}' to allow services to run without an active login session..."
  loginctl enable-linger "${USERNAME}"

  log "Reloading systemd user daemon to recognize the new user service..."
  systemctl --user daemon-reload

  log "Enabling the service '${file}' for user '${USERNAME}'..."
  systemctl --user enable "${file}"

  log "Starting services..."
  systemctl --user start --now "${file}"
}

main() {
  local start_time=""
  start_time=$(date +%s%N)

  log "Starting setup..."

  setup_dependencies
  setup_app_dirs
  setup_networking

  setup_model

  # Fetch configuration files from the application repository if GITHUB_REPO is provided.
  if [[ -n "${GITHUB_REPO}" ]]; then
    fetch_configuration
  else
    log "No application repository provided, skipping configuration fetch..."
  fi

  setup_env_file

  # * Service composes the containers and enables them to start on boot
  enable_services

  local end_time=""
  end_time=$(date +%s%N)
  local time_ns=$((end_time - start_time))
  local time_ms=$((time_ns / 1000000))

  log "Model volume: ${HF_MODELS_VOLUME} (model at /hf/models/ when mounted)"
  log "HF cache volume: ${HF_CACHE_VOLUME}"

  log "Script completed in [${time_ms}ms]"

}

main
