# Setting up the server

Quick guide to deploying the DTA server.

## System requirements

The server must be running **Ubuntu 24.04 LTS** or later, as it requires the package [`podman-compose`](https://packages.ubuntu.com/source/noble/podman-compose), which is not available in earlier versions. Running the script on **Ubuntu 22.04 LTS** or earlier will attempt to install and use the package [`docker-compose`](https://packages.ubuntu.com/source/jammy/docker-compose) instead.

The [`setup`](../setup.sh) script will automatically install and upgrade all required dependencies[^1].

[^1]: `curl` is needed to download the setup script, and many distributions/images already include it. Install it only if missing, since the script cannot install `curl` before it is downloaded.

## Download

- Download the setup script within the remote server with `curl`:

```bash
curl -fsSLO https://raw.githubusercontent.com/aalto-speech/dta-server/refs/heads/main/setup.sh
chmod +x ./setup.sh
```

> [!NOTE]
> If `curl` is not installed, install it first:
>
> ```bash
> sudo apt update
> sudo apt install -y curl
> ```

## Install

> [!WARNING]
> For now the user **NEEDS** to log in to `ghcr.io` with podman to be able to pull the needed container images before running the setup command!
>
> ```bash
> sudo apt update
> sudo apt install -y podman
> podman login ghcr.io
> ```

- To see script options and environment variable overrides, run:

  ```bash
  ./setup.sh -h
  ```

- To run the setup script without saving sensitive tokens within the bash or command history, use the following commands:

  ```bash
  set +o history
  ./setup.sh -R -r aalto-speech/dta-server:main \
             -e ACME_EMAIL=john.smith@example.com \
             -e DOMAIN=example.com \
             -e APP_ENV=production \
             -e SERVER_DELETE_KEY=your-secret-delete-key
  set -o history
  ```

### Staging server

A staging server runs the `:staging` images (built from the `dev` branch) instead
of `:latest`, and typically has no GPU. Differences from the production install:

- Download the setup script (and later, config files) from the **`dev`** branch, and pass `-r aalto-speech/dta-server:dev`:

  ```bash
  curl -fsSLO https://raw.githubusercontent.com/aalto-speech/dta-server/refs/heads/dev/setup.sh
  chmod +x ./setup.sh
  ./setup.sh -R -r aalto-speech/dta-server:dev \
             -e ACME_EMAIL=<your-email> \
             -e DOMAIN=<staging-domain> \
             -e APP_ENV=staging \
             -e SERVER_DELETE_KEY=$(openssl rand -hex 32)
  ```

- After setup, append the staging overrides to `~/.config/dta/env`:

  ```bash
  DTA_TAG=staging     # run the :staging images, not :latest
  DTA_DEVICE=cpu      # no GPU on staging: model loads in ~10 min, scoring takes 30-60 s
  ASA_TIMEOUT=300     # give the app time to wait for CPU scoring
  ```

- On a RAM-tight staging VM (< 32 GB), add swap before starting the stack — the
  scorer keeps ~10 GB resident on CPU:

  ```bash
  sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```

### Big storage volume

Both servers should keep podman's storage on a dedicated **500 GB block volume**, not the
~80 GB root disk: the inference image is ~10 GB, the model weights ~16 GB, and the
database volume grows with every audio upload. Production was migrated 2026-08-02;
staging should do the same once its volume is attached (`setup.sh` does **not** do this).

With the volume attached as `/dev/vdb`, once:

```bash
sudo parted -s /dev/vdb mklabel gpt mkpart primary ext4 1MiB 100%
sudo mkfs.ext4 -L dta-storage /dev/vdb1
sudo mkdir -p /srv/dta-storage
echo "UUID=$(sudo blkid -s UUID -o value /dev/vdb1) /srv/dta-storage ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /srv/dta-storage
sudo mkdir -p /srv/dta-storage/containers/storage /srv/dta-storage/backups
sudo chown -R ubuntu:ubuntu /srv/dta-storage/containers /srv/dta-storage/backups

mkdir -p ~/.config/containers
cat > ~/.config/containers/storage.conf <<'EOF'
[storage]
driver = "overlay"
graphroot = "/srv/dta-storage/containers/storage"
EOF
```

> [!IMPORTANT]
> Changing `graphroot` makes podman see an **empty** storage: do this on a fresh server
> before `setup.sh`, or on an existing one export/back up the database volume first, stop
> the stack, then re-pull images and re-fetch weights afterwards. Data paths after the
> move are documented in [DATA.md](./DATA.md).

### GPU runtime (production only)

The production VM has a **Tesla P100 (Pascal)** — see the warning in
[WORKFLOW.md](./WORKFLOW.md#the-production-gpu-is-pascal) before touching torch versions.
Fresh-server GPU setup (staging skips all of this and runs `DTA_DEVICE=cpu`):

```bash
sudo apt install -y nvidia-driver-570-server "linux-headers-$(uname -r)"   # installs the 580 branch, last with Pascal support
# NVIDIA container toolkit is not in the Ubuntu archive:
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

**podman 4.9 (Ubuntu 24.04) cannot parse the CDI spec that toolkit ≥ 1.19 generates**
(spec version 0.7.0 / `additionalGids`) — every `--device nvidia.com/gpu=all` then fails
with `unresolvable CDI devices`. Production carries a post-processing script
`/usr/local/sbin/cdi-podman-compat.sh` (strips `additionalGids`, downgrades the version
field) hooked as `ExecStartPost` into `nvidia-cdi-refresh.service` so driver upgrades
stay compatible. Copy both from production when provisioning a new GPU server, then verify:

```bash
podman run --rm --device nvidia.com/gpu=all docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

In `~/dta/compose.yaml`, give the inference service the GPU with the CDI form
(the commented `deploy.resources` block is unreliable under podman-compose):

```yaml
    devices:
      - nvidia.com/gpu=all
```

### Model weights (both staging and production)

The speech scorer's weights (~15.9 GB) are **not** in any container image. Fetch
them once into the `asa-weights` podman volume (resumable if interrupted):

```bash
curl -fsSLO https://raw.githubusercontent.com/aalto-speech/dta-server/refs/heads/main/inference/deploy/fetch_weights.sh
chmod +x fetch_weights.sh
./fetch_weights.sh          # optionally pin: ASA_WEIGHTS_REVISION=<commit-sha> ./fetch_weights.sh
systemctl --user restart dta-compose.service
```

The inference container verifies at startup that the weights match the image and
refuses to serve on a mismatch. See [../inference/docs/WEIGHTS.md](../inference/docs/WEIGHTS.md).

> [!TIP]
> You can generate an API key by running the following command:
>
> ```bash
> openssl rand -hex 32
> ```

- If you suspect that tokens may have been accidentally saved to the bash or command history, clear them with:

  ```bash
  echo "" > ~/.bash_history && history -c
  ```

## Updating the file

The `setup` script handles the automatic installation of system packages, so please add them to the setup script.

The packages are defined within the `setup_dependencies()` function, in the `packages` variable.

The script runs the `main()` function, so check it out to see what it does (_the `main()` function is at the bottom of the file!_).

## Useful commands and references

This section collects a few useful commands and references for managing the deployment after setup.

### Miscellaneous

- Show users that have lingering enabled:

  ```bash
  loginctl list-users
  ```

### Environment variables file

By default, the `env` file is located at `/home/ubuntu/.config/dta/env`. It is generated by the `setup_env_file()` function in the [`setup`](../setup.sh) script.

The environment variables written to the `env` file are documented in the [Defaults](#defaults) section and shown by `./setup.sh -h`.

### Service

By default, the user service file is located at `/home/ubuntu/.config/systemd/user/dta-compose.service`.

- Reload the systemd daemon

  ```bash
  systemctl --user daemon-reload
  ```

- See if the [`dta-compose.service`](../dta-compose.service) service is enabled:

  ```bash
  systemctl --user status dta-compose.service
  ```

- Start the service
  ```bash
  systemctl --user start dta-compose.service
  ```
- Restart/reload the service

  ```bash
  systemctl --user reload dta-compose.service
  ```

- Stop the service
  ```bash
  systemctl --user stop dta-compose.service
  ```

## Defaults

This section lists the setup script's default environment variables, grouped into user-facing and advanced options.

### Variables written to the environment file

The setup script writes the value you pass with `-e`, or an empty line if you
pass nothing. The "Effective default" column shows what the stack falls back to
when the line is left empty.

| Variable               | Effective default | Description                                                          |
| ---------------------- | ----------------- | -------------------------------------------------------------------- |
| `ACME_EMAIL`           | empty             | Email address used for ACME registration.                            |
| `DOMAIN`               | `localhost`       | Domain name used by Caddy.                                           |
| `UPSTREAM`             | `dta:8000`        | Upstream address used by Caddy.                                      |
| `APP_ENV`              | `development`     | Application environment (`staging` or `production` on servers).      |
| `DATABASE`             | `/data/dta.db`    | Absolute path to the application database.                           |
| `AUDIO_SAVE_DIR`       | `/data/audio`     | Directory for saving audio files.                                    |
| `LOGS_SAVE_DIR`        | `/data/logs`      | Directory for saving log files.                                      |
| `LOG_LEVEL`            | `INFO`            | Logging level (compose default; the app alone defaults to WARNING).  |
| `SERVER_DELETE_KEY`    | empty             | Authorises `DELETE /users` — the only thing it opens. Required in production. Renamed from `ADMIN_API_KEY` in v1.2.0; the old name is still read, with a startup warning. |
| `MIN_COHORT_SIZE`      | `100`             | Minimum cohort size for analytics or assessments.                    |
| `MIN_USER_ASSESSMENTS` | `3`               | Minimum number of user assessments required.                         |
| `DTA_TAG`              | `latest`          | Image tag both containers run (`staging` on staging servers).        |
| `DTA_DEVICE`           | `cuda`            | Device for the speech scorer (`cpu` on staging without GPU).         |
| `DTA_AUTOCAST_DTYPE`   | `bfloat16`        | Scorer autocast dtype. Production sets `float16`: its P100 (Pascal) has no bf16, and fp32 halves throughput. |
| `ASA_TIMEOUT`          | `60`              | App-side timeout (s) for one scoring call (`300` on CPU staging).    |
| `CLIENT_API_KEY`       | empty             | Shared key the mobile app sends as `X-Client-Key` on `/request/user`. Empty = header ignored. Ships inside the APK, so it deters casual scripting only — keep it distinct from `SERVER_DELETE_KEY`. |
| `DTA_RELEVANCE_CHECK`  | `1`               | Topical-relevance judge (`content` block). `0` serves scores without it; costs ~0.7 s/request on the P100. |
| `DTA_RELEVANCE_OFF_TOPIC_MIN_CONFIDENCE` | `0.6` | Below this probability an `off_topic` verdict is returned as `partial`. Raise to be more cautious. |

### Advanced variables (only used during setup)

| Variable            | Default                         | Description                                                                |
| ------------------- | ------------------------------- | -------------------------------------------------------------------------- |
| `HF_TOKEN`          | empty                           | Hugging Face token for private repositories and higher rate limits.        |
| `GITHUB_TOKEN`      | empty                           | GitHub token for private repository access and higher rate limits.         |
| `NETWORK_INTERFACE` | empty, auto-detected at runtime | Network interface used for iptables rules.                                 |
| `CADDY_LOGS_VOLUME` | `caddy-logs`                    | Podman volume name for Caddy logs.                                         |
| `TARGET_OS_VERSION` | `24.04`                         | Target Ubuntu version used to choose `podman-compose` vs `docker-compose`. |
| `MODEL_REPO`        | empty                           | Hugging Face model repository to download.                                 |
| `MODEL_REV`         | `main`                          | Model revision, such as a branch, tag, or commit.                          |
| `HF_MODELS_VOLUME`  | `hf-models`                     | Podman volume name for downloaded models.                                  |
| `HF_CACHE_VOLUME`   | `hf-cache`                      | Podman volume name for Hugging Face cache.                                 |
| `USERNAME`          | `ubuntu`                        | Non-root user that runs and manages the application.                       |
| `USER_PATH`         | `/home/ubuntu`                  | Home directory used for application files and configs.                     |
| `APP_PATH`          | `/home/ubuntu/dta`              | Directory where application code and configuration are stored.             |
| `GITHUB_REPO`       | `aalto-speech/dta-server`       | GitHub repository used to fetch configuration files.                       |
| `GITHUB_REPO_REF`   | `main`                          | GitHub reference used when fetching configuration files.                   |
| `SERVICE_PATH`      | `dta-compose.service`           | Systemd user service file fetched from the repository.                     |
