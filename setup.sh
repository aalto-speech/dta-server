#!/usr/bin/env bash
set -euo pipefail

VERBOSE="${VERBOSE:-1}"
DRY_RUN="${DRY_RUN:-0}"
declare -a CONFIG_FILES=()

usage () {
  cat <<'EOF'
Usage: [ENV VARS] ./setup.sh [OPTIONS]

Options:
  -q, --quiet                             Disable progress logs
  -n, --dry-run                           Show what would be executed without running commands
  -i, --interface <interface>             Network interface to apply iptables rules on (default: auto-detected)
  -u, --user <username>                   Non-root user to run the application (default: saysuomi)
  -p, --user-path <path>                  Home directory for the application user (default: /home/<username>)
  -r, --app-repo <user/repo[:ref]>        Git repository and optional revision. If no reference given, defaults to main (default: aalto-speech/dta-server:main)
  -m, --model <user/repo[:rev]>           Hugging Face repository and optional revision. If no revision given, defaults to main (default: Usin2705/CaptainA_v0:main)
  -d, --compose-file <path>               Path to the container compose file (default: compose.yaml)
  -c, --config-files <path> [<path> ...]  Path(s) to configuration files to fetch from the application repository (default: Caddyfile compose.yaml)
  -h, --help                              Show this help

Environment overrides:
  Model:
    HF_MODELS_VOLUME=hf-models  Podman volume name for downloaded models
    HF_CACHE_VOLUME=hf-cache    Podman volume name for downloaded cache
    HF_TOKEN=                   Hugging Face API token (for private repositories and increased rate limits)

  Build:
    GITHUB_TOKEN=               GitHub token (for private repository access and increased rate limits)
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
    -i|--interface)
      NETWORK_INTERFACE="$2"
      shift 2
      ;;
    -u|--user)
      USERNAME="$2"
      shift 2
      ;;
    -p|--user-path)
      USER_PATH="$2"
      shift 2
      ;;
    -r|--app-repo)
      GIT_REPO="${2%%:*}"
      if [[ "$2" == *:* ]]; then
        GIT_REPO_REF="${2#*:}"
      fi
      shift 2
      ;;
    -m|--model)
      MODEL_REPO="${2%%:*}"
      MODEL_NAME="${MODEL_REPO##*/}"
      if [[ "$2" == *:* ]]; then
        MODEL_REV="${2#*:}"
      fi
      shift 2
      ;;
    -d|--compose-file)
      COMPOSE_FILE="$2"
      shift 2
      ;;
    -c|--config-files)
      shift
      while [[ $# -gt 0 ]] && [[ "$1" != -* ]]; do
        CONFIG_FILES+=("$1")
        shift
      done
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

validate_input () {
  local var_name="$1"
  local var_value="$2"
  local pattern="$3"

  if [[ ! "${var_value}" =~ ${pattern} ]]; then
    echo "Error: Invalid ${var_name}: '${var_value}'" >&2
    echo "Must match pattern: ${pattern}" >&2
    exit 1
  fi
}

validate_path () {
  local path="$1"
  # Prevent path traversal attacks
  if [[ "${path}" == *".."* ]] || [[ "${path}" == /* ]]; then
    echo "Error: Invalid path '${path}' - absolute paths and path traversal not allowed" >&2
    exit 1
  fi
}

# Default environment variables. Use CLI options to override.
NETWORK_INTERFACE="${NETWORK_INTERFACE:-}"  # Network interface to apply iptables rules on (auto-detected if not set)

# Model settings
MODEL_REPO="${MODEL_REPO:-Usin2705/CaptainA_v0}"  # LLM repository
MODEL_REV="${MODEL_REV:-main}"  # LLM revision (branch, tag, or commit)
MODEL_NAME="${MODEL_NAME:-CaptainA_v0}" # LLM directory name the model will be stored under in the volume (e.g., /hf/models/${MODEL_NAME})
# Sanitize MODEL_NAME to prevent path traversal
MODEL_NAME="${MODEL_NAME##*/}"  # Remove any path components
MODEL_NAME="${MODEL_NAME//[^a-zA-Z0-9_-]/}"  # Remove special characters

HF_MODELS_VOLUME="${HF_MODELS_VOLUME:-hf-models}" # Podman volume name for storing downloaded models
HF_CACHE_VOLUME="${HF_CACHE_VOLUME:-hf-cache}"  # Podman volume name for storing Hugging Face cache
HF_TOKEN="${HF_TOKEN:-}"  # Hugging Face API token (optional, for private repositories and increased rate limits)

# Application settings
USERNAME="${USERNAME:-saysuomi}"  # Non-root user to run the application and manage resources
USER_PATH="${USER_PATH:-/home/${USERNAME}}"  # Root directory for the application user (e.g., for logs, data, etc.)
GIT_REPO="${GIT_REPO:-aalto-speech/dta-server}" # Git repository for fetching configuration files
GIT_REPO_REF="${GIT_REPO_REF:-main}"  # Git reference (branch, tag, or commit)
GITHUB_TOKEN="${GITHUB_TOKEN:-}"  # GitHub token (optional, for private repository access and increased rate limits)

# Misc. settings
COMPOSE_FILE=${COMPOSE_FILE:-compose.yaml}  # Path to the container compose file
if [[ ${#CONFIG_FILES[@]} -eq 0 ]]; then
  CONFIG_FILES=(Caddyfile ${COMPOSE_FILE})  # Default configuration files to fetch from the repository
fi

# Validate critical inputs to prevent command injection
validate_input "GIT_REPO" "${GIT_REPO}" '^[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+$'
validate_input "GIT_REPO_REF" "${GIT_REPO_REF}" '^[a-zA-Z0-9/_.-]+$'
validate_input "MODEL_REPO" "${MODEL_REPO}" '^[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+$'
validate_input "MODEL_REV" "${MODEL_REV}" '^[a-zA-Z0-9/_.-]+$'
validate_input "USERNAME" "${USERNAME}" '^[a-z_][a-z0-9_-]*$'

# Validate config file paths
for config_file in "${CONFIG_FILES[@]}"; do
  validate_path "${config_file}"
done


setup_dependencies () {
  # Install required dependencies

  log "Updating and installing required dependencies..."
  run_cmd sudo apt update
  run_cmd sudo apt install -y podman podman-compose git curl iptables-persistent
}


setup_user () {
  # Create a non-root user for running the application and managing resources

  if id -u "${USERNAME}" >/dev/null 2>&1; then
    log "User '${USERNAME}' already exists. Skipping user creation."
  else
    log "Creating user '${USERNAME}'..."
    run_cmd sudo useradd -m -s /bin/bash "${USERNAME}"
    log "User '${USERNAME}' created successfully."
  fi
}


setup_app_dirs () {
  # Create application directory structure under the user

  # ? Should 'cache' and 'models' be symlinks to the volume points?
  local dirs=(
    "${USER_PATH}/app/cache"
    "${USER_PATH}/app/conf.d"
    "${USER_PATH}/app/data"
    "${USER_PATH}/app/logs"
    "${USER_PATH}/app/models"
  )

  log "Creating app user directories if they do not exist at '${USER_PATH}'..."
  run_cmd sudo mkdir -p "${dirs[@]}"

  log "Setting ownership of '${USER_PATH}/app' to '${USERNAME}'..."
  run_cmd sudo chown -R "${USERNAME}:${USERNAME}" "${USER_PATH}/app"
}


setup_networking () {
  # Setup iptables rules to redirect ports 80 and 443 to 8080 and 8443 respectively
  # * This is needed since binding to privileged ports (<1024) typically requires root permissions, which is not ideal for containerized applications ran through podman. By redirecting to higher ports, the application can run without elevated privileges while still being accessible on standard HTTP/HTTPS ports.

  log "Setting up iptables rules for port redirection..."

  # Save current iptables rules to restore on error and prevent lockout, and set up trap for cleanup
  local iptables_backup_dir=""
  local iptables4_backup=""
  local iptables6_backup=""
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "[DRY-RUN] Would save iptables rules to temporary backup directory."
  else
    iptables_backup_dir="$(mktemp -d)"
    iptables4_backup="$(mktemp -p "${iptables_backup_dir}")"
    iptables6_backup="$(mktemp -p "${iptables_backup_dir}")"
    sudo iptables-save > "${iptables4_backup}"
    sudo ip6tables-save > "${iptables6_backup}"

    # Set up trap to restore iptables on error, and clean up
    trap 'trap - ERR; log "Error occurred, restoring iptables..."; sudo iptables-restore < "${iptables4_backup}"; sudo ip6tables-restore < "${iptables6_backup}"; if [[ -n "${iptables_backup_dir}" && -d "${iptables_backup_dir}" ]]; then rm -rf "${iptables_backup_dir}"; fi; unset iptables_backup_dir iptables4_backup iptables6_backup' ERR
  fi

  # 4. Interface autodetection (fallback to eth0)
  local interface="${NETWORK_INTERFACE:-}"
  if [[ -z "$interface" ]]; then
    interface=$(ip route | awk '/default/ {print $5; exit}')
    if [[ -z "$interface" ]]; then
      interface="eth0"
      log "[WARN] Could not auto-detect network interface, defaulting to 'eth0'."
    else
      log "Auto-detected network interface: '$interface'"
    fi
  else
    log "Using configured network interface: '$interface'"
  fi

  # Check if rules already exist to avoid duplicates
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "[DRY-RUN] Would check for existing iptables rules for interface '${interface}' and ports 80/443."
  else
    local rule_80_exists=0
    local rule_443_exists=0
    if sudo iptables -t nat -C PREROUTING -i "${interface}" -p tcp --dport 80 -j REDIRECT --to-ports 8080 2>/dev/null; then
      rule_80_exists=1
    fi
    if sudo iptables -t nat -C PREROUTING -i "${interface}" -p tcp --dport 443 -j REDIRECT --to-ports 8443 2>/dev/null; then
      rule_443_exists=1
    fi
    if [[ $rule_80_exists -eq 1 && $rule_443_exists -eq 1 ]]; then
      log "Port redirection rules already exist, skipping..."
      trap - ERR
      unset iptables_backup_dir iptables4_backup iptables6_backup
      return 0
    fi
  fi

  # Redirect port 80 to 8080
  run_cmd sudo iptables -t nat -A PREROUTING -i "${interface}" -p tcp --dport 80 -j REDIRECT --to-ports 8080
  run_cmd sudo iptables -t nat -A OUTPUT -o lo -p tcp --dport 80 -j REDIRECT --to-ports 8080
  run_cmd sudo iptables -A INPUT -i "${interface}" -p tcp --dport 8080 -j ACCEPT

  # Redirect port 443 to 8443
  run_cmd sudo iptables -t nat -A PREROUTING -i "${interface}" -p tcp --dport 443 -j REDIRECT --to-ports 8443
  run_cmd sudo iptables -t nat -A OUTPUT -o lo -p tcp --dport 443 -j REDIRECT --to-ports 8443
  run_cmd sudo iptables -A INPUT -i "${interface}" -p tcp --dport 8443 -j ACCEPT

  log "Saving iptables rules to '/etc/iptables/rules.v[4|6]'..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "[DRY-RUN] Would execute: sudo iptables-save | sudo tee /etc/iptables/rules.v4"
    log "[DRY-RUN] Would execute: sudo ip6tables-save | sudo tee /etc/iptables/rules.v6"
  else
    sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null
    sudo ip6tables-save | sudo tee /etc/iptables/rules.v6 >/dev/null

    # Clean up backup and remove trap
    if [[ -n "${iptables_backup_dir}" && -d "${iptables_backup_dir}" ]]; then
      rm -rf "${iptables_backup_dir}"
    fi
    trap - ERR
    unset iptables_backup_dir iptables4_backup iptables6_backup
  fi
}


setup_model () {
  # Downloads the specified LLM into podman volumes for persistent storage and caching

  log "Creating volumes '${HF_MODELS_VOLUME}' and '${HF_CACHE_VOLUME}' if they do not exist..."
  run_cmd podman volume create --ignore "${HF_MODELS_VOLUME}" >/dev/null
  run_cmd podman volume create --ignore "${HF_CACHE_VOLUME}" >/dev/null

  # Run a temporary container to download the model and cache into the volumes
  log "Downloading model '${MODEL_REPO}:${MODEL_REV}' into volume '${HF_MODELS_VOLUME}'..."
  local image_name="docker.io/python:3.14-slim"

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "[DRY-RUN] Would execute: podman run (model download)..."
  else
    local podman_env_args=(
      -e HF_HOME=/hf
      -e HUGGINGFACE_HUB_CACHE=/hf/cache/hub
      -e TRANSFORMERS_CACHE=/hf/cache/transformers
    )

    # Make HF_TOKEN available inside the container if provided using secure temp file
    local temp_token_dir=""
    local temp_token_file=""
    if [[ -n "${HF_TOKEN}" ]]; then
      temp_token_dir="$(mktemp -d)"
      chmod 700 "${temp_token_dir}"
      temp_token_file="$(mktemp -p "${temp_token_dir}")"
      echo "HF_TOKEN=${HF_TOKEN}" > "${temp_token_file}"
      chmod 600 "${temp_token_file}"
      podman_env_args+=(--env-file "${temp_token_file}")

      # Set up trap to ensure token file cleanup
      trap 'trap - EXIT INT TERM; if [[ -n "${temp_token_dir}" && -d "${temp_token_dir}" ]]; then rm -rf "${temp_token_dir}"; fi; unset temp_token_dir temp_token_file' EXIT INT TERM
    fi

    podman run --rm --pull=always \
    --userns=keep-id \
    "${podman_env_args[@]}" \
    -v "${HF_MODELS_VOLUME}":/hf/models:Z \
    -v "${HF_CACHE_VOLUME}":/hf/cache:Z \
    "${image_name}" \
    bash -lc \
    "pip install -U 'huggingface_hub[cli]' >/dev/null \
      && huggingface-cli download '${MODEL_REPO}' \
        --revision '${MODEL_REV}' \
        --local-dir '/hf/models/${MODEL_NAME}' \
        --local-dir-use-symlinks False"

    unset podman_env_args

    # Clean up token directory immediately after use
    if [[ -n "${temp_token_dir}" && -d "${temp_token_dir}" ]]; then
      trap - EXIT INT TERM
      rm -rf "${temp_token_dir}"
      unset temp_token_dir temp_token_file
    fi
  fi

  log "Cleaning up temporary containers and images '${image_name}'..."
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    podman rm -f -i "${image_name}" >/dev/null || true
    podman rmi -f -i "${image_name}" >/dev/null || true
  fi

  unset image_name

  log "Model downloaded to volume '${HF_MODELS_VOLUME}'."
  log "Cache stored in volume '${HF_CACHE_VOLUME}'."
}


fetch_file () {
  local path="$1"
  local out="$2"

  if [[ ${DRY_RUN} -eq 1 ]]; then
    log "[DRY-RUN] Would fetch file '${path}' from repository '${GIT_REPO}:${GIT_REPO_REF}' to '${out}'."
    return 0
  fi

  local curl_args=(-fsSL -H "Accept: application/vnd.github.raw")

  # Use secure token handling if GITHUB_TOKEN is provided
  local curl_config_dir=""
  local curl_config_file=""

  if [[ -n "${GITHUB_TOKEN}" ]]; then
    curl_config_dir="$(mktemp -d)"
    chmod 700 "${curl_config_dir}"
    curl_config_file="$(mktemp -p "${curl_config_dir}")"

    # Write token to config file to avoid exposing it in process listings
    cat > "${curl_config_file}" <<CURL_CONFIG_EOF
header = "Authorization: Bearer ${GITHUB_TOKEN}"
CURL_CONFIG_EOF
    chmod 600 "${curl_config_file}"
    curl_args+=(--config "${curl_config_file}")

    # Set up trap to ensure cleanup
    trap 'trap - EXIT INT TERM ERR; if [[ -n "${curl_config_dir}" && -d "${curl_config_dir}" ]]; then rm -rf "${curl_config_dir}"; fi; unset curl_config_dir curl_config_file curl_args' EXIT INT TERM ERR
  fi

  curl "${curl_args[@]}" \
    "https://api.github.com/repos/${GIT_REPO}/contents/${path}?ref=${GIT_REPO_REF}" \
    -o "${out}"

  unset curl_args

  # Clean up token config
  if [[ -n "${curl_config_dir}" && -d "${curl_config_dir}" ]]; then
    trap - EXIT INT TERM ERR
    rm -rf "${curl_config_dir}"
    unset curl_config_dir curl_config_file
  fi
}


fetch_configuration () {
  # Fetch configuration files from the application repository to the server

  log "Fetching files from repository '${GIT_REPO}:${GIT_REPO_REF}'..."

  if [[ ${DRY_RUN} -eq 1 ]]; then
    log "[DRY-RUN] Would fetch configuration files: ${CONFIG_FILES[*]}"
    return 0
  fi

  local conf_dir="${USER_PATH}/app/conf.d"

  for config_file in "${CONFIG_FILES[@]}"; do
    local out="${conf_dir}/${config_file##*/}"
    log "Fetching file '${config_file}' to '${out}'..."
    run_cmd fetch_file "${config_file}" "${out}"
  done

  unset conf_dir
}


fetch_app () {
  # TEMPORARY FUNCTION! Fetch the application source code from the repository to the server
  # ! Once the configuration files are in place and the application image can be built in CI/CD use fetch_configuration instead and remove this function!

  local temp_conf_path=""
  local app_repo_path="${USER_PATH}/app/repo"
  if [[ -d $(app_repo_path) ]]; then
    sudo rm -rf "${app_repo_path}"
  fi

  log "Fetching application source code from 'https://github.com/${GIT_REPO}:${GIT_REPO_REF}' to a temporary directory..."

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "[DRY-RUN] Would fetch application source code from 'https://github.com/${GIT_REPO}:${GIT_REPO_REF}' to '${app_repo_path}'..."
  else
    temp_conf_path="$(mktemp -d)"
    cd ${temp_conf_path}

    # Use GIT_ASKPASS for secure token handling if GITHUB_TOKEN is provided
    local git_askpass_script=""
    local git_askpass_dir=""

    if [[ -n "${GITHUB_TOKEN}" ]]; then
      git_askpass_dir="$(mktemp -d)"
      chmod 700 "${git_askpass_dir}"
      git_askpass_script="$(mktemp -p "${git_askpass_dir}")"

      # Embed token directly in script
      cat > "${git_askpass_script}" <<ASKPASS_EOF
#!/bin/sh
echo "${GITHUB_TOKEN}"
ASKPASS_EOF
      chmod 500 "${git_askpass_script}"
      export GIT_ASKPASS="${git_askpass_script}"

      # Set up trap to ensure cleanup even if interrupted
      trap 'trap - EXIT INT TERM ERR; if [[ -n "${git_askpass_dir}" && -d "${git_askpass_dir}" ]]; then rm -rf "${git_askpass_dir}"; fi; unset GIT_ASKPASS git_askpass_script git_askpass_dir' EXIT INT TERM ERR
    fi

    git init >/dev/null
    git remote add origin "https://github.com/${GIT_REPO}.git" >/dev/null
    git fetch --depth 1 origin "${GIT_REPO_REF}" >/dev/null
    git checkout FETCH_HEAD -- . >/dev/null
    rm -rf .git

    cd ~

    # Move the fetched files to the actual configuration directory
    log "Moving fetched files to '${app_repo_path}' and setting ownership to '${USERNAME}'..."
    sudo mv "${temp_conf_path}/" "${app_repo_path}/"
    sudo chown -R "${USERNAME}:${USERNAME}" "${app_repo_path}"

    # Clean up the askpass script and directory
    if [[ -n "${git_askpass_dir}" && -d "${git_askpass_dir}" ]]; then
      trap - EXIT INT TERM ERR
      rm -rf "${git_askpass_dir}"
      unset GIT_ASKPASS git_askpass_script git_askpass_dir
    fi
  fi
}


build_app () {
  log "Building application from compose file '${COMPOSE_FILE}'..."
  run_cmd podman compose build -f "${COMPOSE_FILE}" --pull --quiet
  log "Run the application with: podman compose -f ${COMPOSE_FILE} up -d"
}


main () {
  local start_time=$(date +%s%N)

  if [[ "${DRY_RUN}" == "1" ]]; then
    log "Starting setup in DRY-RUN mode (no commands will be executed)..."
  else
    log "Starting setup..."
  fi

  setup_dependencies

  setup_user
  setup_app_dirs
  setup_networking

  # setup_model
  # fetch_configuration # ! See fetch_app comments!
  fetch_app # ! If no GITHUB_TOKEN provided, fetch_app can not fetch the application code from a private repository.

  local conf_dir="${USER_PATH}/app/conf.d"
  local repo_dir="${USER_PATH}/app/repo"
  if [[ ! -d "${repo_dir}" || -z "$(sudo ls -A "${repo_dir}" 2>/dev/null)" ]]; then
    log "Copy the application code manually to the server with 'scp <local_path> <user>@<host>:<remote_path>'. Exiting..."
    exit 0
  fi

  build_app

  local end_time=$(date +%s%N)
  local time_ns=$((end_time - start_time))
  local time_ms=$((time_ns / 1000000))

  log "Model volume: ${HF_MODELS_VOLUME} (model at /hf/models/${MODEL_NAME} when mounted)"
  log "HF cache volume: ${HF_CACHE_VOLUME}"

  log "Script completed in [${time_ms}ms]"
}

main
