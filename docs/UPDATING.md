# Updating the DTA Server and Configuration Files

This quick guide describes the recommended process for updating the DigiTala in Action (DTA) server application and its configuration files on the deployment server.

## Prerequisites

- Access to the deployment server (SSH or console)
- Sufficient privileges to stop and start services, and update files

## Updating the DTA Server

1. Connect to the server:

   ```bash
   ssh -i ~/.ssh/keyname.pem <user>@<floating-ip>
   ```

2. Pull the latest image:

   ```bash
   podman pull ghcr.io/aalto-speech/dta-server:latest
   ```

3. Restart the service:

   ```bash
   systemctl --user stop dta-compose.service
   systemctl --user start dta-compose.service
   ```

4. Verify the update:
   - Check the application logs for errors:
     ```bash
     podman logs -f dta caddy
     ```
   - Test the main endpoints (e.g., `/ping`, `/status`).
   - Confirm that the application is running as expected.

## Updating the Configuration Files

The configuration files (`Caddyfile`, `compose.yaml`, `dta-compose.service`, etc.) are only fetched during the initial setup (with the `setup.sh` script), so there is no set plan for updating the files. However, you can update them by re-fetching them with `curl`.

1. Connect to the server.
2. Go to the `dta` directory:

   ```bash
   cd ~/dta
   ```

3. Fetch the desired files with `curl`:

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/aalto-speech/dta-server/refs/heads/main/Caddyfile
   curl -fsSLO https://raw.githubusercontent.com/aalto-speech/dta-server/refs/heads/main/compose.yaml
   ```

4. Restart the service if needed.

## References

- [Development guide](/docs/DEVELOPMENT.md)
- [Setup instructions](/docs/SETUP.md)
- [API reference](/docs/API.md)
- [Pouta deployment](/docs/POUTA.md)
