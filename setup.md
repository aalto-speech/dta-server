> [!IMPORTANT]
> This documentation will change in the future, so remember to re-check this page on new updates!

# Setting up the server

Quick guide to deploying the DTA server.

## System requirements

The server must be running **Ubuntu 24.04 LTS** or later, as it requires the package [`podman-compose`](https://packages.ubuntu.com/source/noble/podman-compose), which is not available in earlier versions. Running the script on **Ubuntu 22.04 LTS** or earlier will attempt to install and use the package [`docker-compose`](https://packages.ubuntu.com/source/jammy/docker-compose) instead.

The [`setup`](./setup.sh) script will automatically install and upgrade all required dependencies.

## Copying the setup script

- Copy the setup script to the remote server by running the following command on your local machine. Replace `<user>`, `<remote>`, and `<path>` with the appropriate values for your setup.

  ```bash
  scp ./setup.sh <user>@<remote>:<path>
  ```

<details>
<summary>Alternative: Copying the contents</summary>
You can also manually copy the script by displaying its contents and pasting them into a new file on the remote server:

1. On your local machine, copy the contents of the script.

2. On the remote server, create a new file and paste the contents:

   ```bash
   vi <path>/setup.sh
   ```

3. Save and close the file, then make it executable if needed:

   ```bash
   chmod u+x <path>/setup.sh
   ```

</details>

## Running the setup script

> [!WARNING]
> For now the user **NEEDS** to log in to `ghcr.io` with podman to be able to create and launch the service before running the setup command!
>
> ```bash
> sudo apt update
> sudo apt upgrade -y
> sudo apt install podman -y
> podman login ghcr.io
> ```

- To see script options and overrides, run

  ```bash
  ./setup.sh -h
  ```

- To run the setup script without saving sensitive tokens within the bash or command history, use the following commands:

  ```bash
  set +o history
  GITHUB_TOKEN=github_pat HF_TOKEN=hf_pat ./setup.sh
  set -o history
  ```

- If you suspect that tokens may have been saved within the bash or command history, clear them with:

  ```bash
  rm ~/.bash_history && history -c
  ```

# Helpful commands and information

## Misc.

- See if the user has lingering enabled:

  ```bash
  loginctl list-users
  ```

## Service

By default the user service file is located at `/home/<user>/.config/systemd/user/dta-compose.service`

- Reload the systemd daemon

  ```bash
  systemctl --user daemon-reload
  ```

- See if the [`dta-compose.service`](./dta-compose.service) service is enabled:

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
