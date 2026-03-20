#!/usr/bin/env bash
set -euo pipefail

VERBOSE="${VERBOSE:-1}"
DRY_RUN="${DRY_RUN:-0}"
declare -a CONFIG_FILES=()

usage() {
  cat <<'EOF'
Usage: [ENV VARS] ./setup.sh [OPTIONS]

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

  -d, --compose-file <path>
                       Path to the compose file (default: compose.yaml)

  -c, --config-files <path> [<path> ...]
                       Path(s) to configuration files to fetch from the application repository
                       (default: Caddyfile compose.yaml)

  -h, --help           Show this help

Environment overrides:
  Model:
    HF_TOKEN=          Hugging Face API token (for private repositories and increased rate limits)

  Application:
    GITHUB_TOKEN=      GitHub token (for private repository access and increased rate limits)
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
        -u | --user)
            USERNAME="$2"
            shift 2
        ;;
        -p | --user-path)
            USER_PATH="$2"
            shift 2
        ;;
        -r | --repo)
            GIT_REPO="${2%%:*}"
            if [[ "$2" == *:* ]]; then
                GIT_REPO_REF="${2#*:}"
            fi
            shift 2
        ;;
        -m | --model)
            MODEL_REPO="${2%%:*}"
            MODEL_NAME="${MODEL_REPO##*/}"
            if [[ "$2" == *:* ]]; then
                MODEL_REV="${2#*:}"
            fi
            shift 2
        ;;
        -d | --compose-file)
            COMPOSE_FILE="$2"
            shift 2
        ;;
        -c | --config-files)
            shift
            while [[ $# -gt 0 ]] && [[ "$1" != -* ]]; do
                CONFIG_FILES+=("$1")
                shift
            done
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

# Default environment variables. Use CLI options to override.
NETWORK_INTERFACE="${NETWORK_INTERFACE:-}"           # Network interface to apply iptables rules on (auto-detected if not set)
CADDY_LOGS_VOLUME="${CADDY_LOGS_VOLUME:-caddy-logs}" # Podman volume name
TARGET_OS_VERSION="${TARGET_OS_VERSION:-24.04}"      # Target OS version for package selection (e.g., for podman-compose vs docker-compose)

# Model settings
MODEL_REPO="${MODEL_REPO:-}"             # LLM repository
MODEL_REV="${MODEL_REV:-main}"          # LLM revision (branch, tag, or commit)
MODEL_NAME="${MODEL_NAME:-CaptainA_v0}" # LLM directory name the model will be stored under in the volume (e.g., /hf/models/${MODEL_NAME})
# Sanitize MODEL_NAME to prevent path traversal
MODEL_NAME="${MODEL_NAME##*/}"              # Remove any path components
MODEL_NAME="${MODEL_NAME//[^a-zA-Z0-9_-]/}" # Remove special characters

HF_MODELS_VOLUME="${HF_MODELS_VOLUME:-hf-models}" # Podman volume name for storing downloaded models
HF_CACHE_VOLUME="${HF_CACHE_VOLUME:-hf-cache}"    # Podman volume name for storing Hugging Face cache
HF_TOKEN="${HF_TOKEN:-}"                          # Hugging Face API token (optional, for private repositories and increased rate limits)

# Application settings
USERNAME="${USERNAME:-ubuntu}"                  # Non-root user to run the application and manage resources
USER_PATH="${USER_PATH:-/home/${USERNAME}}"     # Root directory for the application user (e.g., for logs, data, etc.)
APP_PATH="${USER_PATH}/dta"                     # Directory where the application code and configuration will be stored
GIT_REPO="${GIT_REPO:-aalto-speech/dta-server}" # Git repository for fetching configuration files
GIT_REPO_REF="${GIT_REPO_REF:-main}"            # Git reference (branch, tag, or commit)
GITHUB_TOKEN="${GITHUB_TOKEN:-}"                # GitHub token (optional, for private repository access and increased rate limits)

# Misc. settings
COMPOSE_FILE=${COMPOSE_FILE:-compose.yaml}        # Path to the container compose file
SERVICE_FILE=${SERVICE_FILE:-dta-compose.service} # Name of the systemd service file to create for managing the application services
if [[ ${#CONFIG_FILES[@]} -eq 0 ]]; then
    CONFIG_FILES=(Caddyfile "${COMPOSE_FILE}" "${SERVICE_FILE}") # Default configuration files to fetch from the repository
fi

# Validate critical inputs to prevent command injection
validate_input "GIT_REPO" "${GIT_REPO}" '^$|^[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+$'
validate_input "GIT_REPO_REF" "${GIT_REPO_REF}" '^[a-zA-Z0-9/_.-]+$'
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
        
        log "Downloading model '${MODEL_REPO}:${MODEL_REV}' into volume '${HF_MODELS_VOLUME}' using a temporary container..."
        podman run --rm --pull=always \
        "${env_args[@]}" \
        -v "${HF_MODELS_VOLUME}":/hf/models:Z \
        -v "${HF_CACHE_VOLUME}":/hf/cache:Z \
        "${image_name}" \
        bash -lc \
        "mkdir -p ${HOME}/.cache ${HOME}/.local && chmod -R 700 ${HOME} && \
      python -m pip install -U pip >/dev/null && \
      pip install -U huggingface_hub >/dev/null && \
      hf download '${MODEL_REPO}' \
        --revision '${MODEL_REV}' \
        --local-dir '/hf/models/${MODEL_NAME}'"
        
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
    "https://api.github.com/repos/${GIT_REPO}/contents/${path}?ref=${GIT_REPO_REF}" \
    -o "${out}"
    
    cleanup
    unset -f cleanup
}

fetch_configuration() {
    # Fetch configuration files from the application repository to the server
    
    log "Fetching files from repository '${GIT_REPO}:${GIT_REPO_REF}'..."
    
    local conf_dir="${APP_PATH}"
    
    for config_file in "${CONFIG_FILES[@]}"; do
        local out="${conf_dir}/${config_file##*/}"
        log "Fetching file '${config_file}' to '${out}'..."
        fetch_file "${config_file}" "${out}"
    done
    
    unset conf_dir
}

fetch_app() {
    # TEMPORARY FUNCTION! Fetch the application source code from the repository to the server
    # ! Once the configuration files are in place and the application image can be built in CI/CD use fetch_configuration instead and remove this function!
    
    cleanup() {
        trap - EXIT INT TERM ERR
        if [[ -n "${git_askpass_dir}" && -d "${git_askpass_dir}" ]]; then
            rm -rf "${git_askpass_dir}"
        fi
        unset GIT_ASKPASS git_askpass_script git_askpass_dir
    }
    
    local git_askpass_dir=""
    local git_askpass_script=""
    local app_repo_path="${APP_PATH}/repo"
    
    if [[ -d "${app_repo_path}" ]]; then
        rm -rf "${app_repo_path}"
    fi
    
    # Create the app repo directory with appropriate permissions
    mkdir -p "${app_repo_path}"
    chmod -R 755 "${app_repo_path}"
    
    # Use GIT_ASKPASS for secure token handling if GITHUB_TOKEN is provided
    if [[ -n "${GITHUB_TOKEN}" ]]; then
        git_askpass_dir="$(mktemp -d)"
        chmod 700 "${git_askpass_dir}"
        git_askpass_script="$(mktemp -p "${git_askpass_dir}")"
        
        # Embed token directly in script
    cat >"${git_askpass_script}" <<ASKPASS_EOF
#!/bin/sh
echo "${GITHUB_TOKEN}"
ASKPASS_EOF
        chmod 500 "${git_askpass_script}"
        export GIT_ASKPASS="${git_askpass_script}"
        
        # Set up trap to ensure cleanup even if interrupted
        trap 'cleanup' EXIT INT TERM ERR
    fi
    
    log "Fetching application source code from 'https://github.com/${GIT_REPO}:${GIT_REPO_REF}' to '${app_repo_path}'..."
    
    git init --quiet "${app_repo_path}" >/dev/null
    cd "${app_repo_path}"
    
    git remote add origin "https://github.com/${GIT_REPO}.git" >/dev/null
    git fetch --depth 1 origin "${GIT_REPO_REF}" >/dev/null
    git checkout FETCH_HEAD -- . >/dev/null
    rm -rf .git
    cd ~
    
    # Clean up the askpass script and directory
    cleanup
}

setup_env_file() {
    local env_path="/home/ubuntu/.config/dta"
    local env_file="${env_path}/env"
    
    mkdir -p "${env_path}"
    
    chmod 700 "$env_path"
    chown -R ubuntu:ubuntu /home/ubuntu/.config/dta
    
    log "Creating env file at '${env_file}' with environment variables for the compose..."
    
  cat >"${env_file}" <<ENV_EOF
# Environment variables for caddy
DOMAIN=${DOMAIN:-}
ACME_EMAIL=${ACME_EMAIL:-}
UPSTREAM=${UPSTREAM:-}
DATABASE=${DATABASE:-dta-server.db}
ADMIN_API_KEY=${ADMIN_API_KEY:-}
ENV_EOF
    chmod 600 /home/ubuntu/.config/dta/env
}

enable_services() {
    log "Enabling services to start on boot..."
    
    # TODO: Get dta-compose.service content from a file in the repository instead of hardcoding it here. This would allow easier maintenance and updates to the service definition without modifying the setup script.
    
    local service_name="${SERVICE_FILE}"
    local service_path="${USER_PATH}/.config/systemd/user/${service_name}"
    mkdir -p "$(dirname "${service_path}")"
    
    log "Creating systemd user service file at '${service_path}'..."
    
  cat >"${service_path}" <<SERVICE_EOF
# Manages the rootless dta-server stack via podman compose (user service).
# Starts containers with: podman compose up -d
# Stops containers with: podman compose down
# Runs in /home/ubuntu/dta after network is online, and can auto-start when enabled.
# /home/ubuntu/.config/systemd/user/dta-compose.service

[Unit]
Description=Rootless dta-server service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=300
Environment=HOME=/home/ubuntu
EnvironmentFile=/home/ubuntu/.config/dta/env
WorkingDirectory=/home/ubuntu/dta
ExecStart=/usr/bin/podman compose up --detach
ExecReload=/usr/bin/podman compose restart
ExecStop=/usr/bin/podman compose down

[Install]
WantedBy=default.target

SERVICE_EOF
    
    log "Enabling lingering for user '${USERNAME}' to allow services to run without an active login session..."
    loginctl enable-linger "${USERNAME}"
    
    log "Reloading systemd user daemon to recognize the new service..."
    systemctl --user daemon-reload
    
    # log "Checking if the service is set up correctly..."
    # systemctl --user status "${service_name}"
    
    log "Enabling the service '${service_name}' for user '${USERNAME}'..."
    systemctl --user enable "${service_name}"
    
    log "Starting services..."
    systemctl --user start --now "${service_name}"
}

main() {
    local start_time=""
    start_time=$(date +%s%N)
    
    log "Starting setup..."
    
    setup_dependencies
    setup_app_dirs
    setup_networking
    
    setup_model
    
    # Fetch configuration files from the application repository if GIT_REPO is provided.
    if [[ -n "${GIT_REPO}" ]]; then
        fetch_configuration
        # fetch_app # ! If no GITHUB_TOKEN provided, fetch_app can not fetch the application code from a private repository.
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
    
    log "Model volume: ${HF_MODELS_VOLUME} (model at /hf/models/${MODEL_NAME} when mounted)"
    log "HF cache volume: ${HF_CACHE_VOLUME}"
    
    log "Script completed in [${time_ms}ms]"
    
}

main
