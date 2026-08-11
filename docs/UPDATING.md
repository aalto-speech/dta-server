# Updating the DTA Server and Configuration Files

This quick guide describes the recommended process for updating the DigiTala in Action (DTA) server application and its configuration files on the deployment server.

## Prerequisites

- Access to the deployment server (SSH or console)
- Sufficient privileges to stop and start services, and update files

> [!NOTE]
> For the full picture of how changes travel from `dev` to staging to production,
> see the [Workflow guide](/docs/WORKFLOW.md).

## Updating the DTA Server

The deployment consists of **two images that move together**: the app
(`ghcr.io/aalto-speech/dta-server`) and the speech scorer
(`ghcr.io/aalto-speech/dta-server/inference`). Staging servers run the
`:staging` tag (built from the `dev` branch); production servers run `:latest`
(built by a GitHub Release). Which tag a server uses is set by `DTA_TAG` in
`~/.config/dta/env`.

1. Connect to the server:

   ```bash
   ssh -i ~/.ssh/keyname.pem <user>@<floating-ip>
   ```

2. Pull the new images (use `:staging` on the staging server):

   ```bash
   podman pull ghcr.io/aalto-speech/dta-server:latest
   podman pull ghcr.io/aalto-speech/dta-server/inference:latest
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
   - Wait for the scorer to come up (`~20 s` on GPU, `~10 min` on CPU staging),
     then run its self-test:
     ```bash
     podman logs -f dta-inference        # until "ready: finnish-v3_..."
     podman exec dta-inference python selftest.py    # must end with PASS
     ```

     On a CPU staging host, do **not** run selftest next to the serving container —
     it loads a second ~10 GB model copy and can OOM the box. See the memory
     warning in [../inference/docs/DEPLOY_STAGING.md](../inference/docs/DEPLOY_STAGING.md).
   - Test the main endpoints (e.g., `/ping`, `/status`) and score one recording.

## Updating the model weights

Weights live in the `asa-weights` podman volume and only change on a retrain —
see [../inference/docs/WEIGHTS.md](../inference/docs/WEIGHTS.md). To update:

```bash
./fetch_weights.sh          # or: ASA_WEIGHTS_REVISION=<commit-sha> ./fetch_weights.sh
systemctl --user restart dta-compose.service
```

The inference container refuses to start if image and weights do not match.

## Schema changes

The app applies `app/schema.sql` **only when the database file does not exist**, so a
schema change reaches an existing database one of two ways.

### Migration script (preferred)

A release that changes the schema ships a script under `scripts/dta_production/`. Run it
with the stack stopped; it is idempotent, backs up first, and verifies afterwards.

```bash
systemctl --user stop dta-compose.service
DB=$(podman volume inspect -f '{{.Mountpoint}}' dta_database)/dta.db
python3 scripts/dta_production/migrate_v1_3_0.py --db "$DB"           # dry run first
python3 scripts/dta_production/migrate_v1_3_0.py --db "$DB" --apply
systemctl --user start dta-compose.service
```

The v1.3.0 migration adds `users.current_cefr_level` and `feedback.updated_at`, collapses
duplicate feedback answers, and backfills `user_cefr_history`. **Collected assessments and
audio survive** — that is the point of migrating rather than recreating.

`tests/test_migration_v1_3_0.py` asserts a migrated database ends up the same shape as one
built fresh from `schema.sql`, so the two paths cannot drift apart silently.

### Recreating the database (last resort)

Some changes cannot be migrated in place — SQLite cannot alter a CHECK constraint, which
is why v1.2.0 required this. **It destroys every assessment and every recording**, so only
do it when the changelog says the release requires it and the data is disposable or
exported first:

```bash
systemctl --user stop dta-compose.service
podman run --rm -v dta_database:/data docker.io/library/alpine \
    rm -f /data/dta.db /data/dta.db-wal /data/dta.db-shm
systemctl --user start dta-compose.service   # app recreates the schema on boot
```

(Confirm the volume name first with `podman volume ls` — it is prefixed with the
compose project name.)

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
