# Where the data lives (and how to get it off the server)

Everything the app stores — the SQLite database, the uploaded audio recordings, and the
model weights — lives in **podman named volumes** on the server. This page says exactly
where those volumes are on disk, and gives copy-paste commands for downloading data to
your own machine.

## Storage layout

Servers use a dedicated **500 GB block volume** mounted at `/srv/dta-storage`, and
rootless podman's whole storage tree (images **and** named volumes) lives on it via
`~/.config/containers/storage.conf`:

```ini
[storage]
driver = "overlay"
graphroot = "/srv/dta-storage/containers/storage"
```

Production was migrated 2026-08-02 (the 80 GB root disk cannot hold the ~10 GB inference
image + ~16 GB weights + growing audio). Staging should get the same volume + config —
setup steps in [SETUP.md](./SETUP.md#big-storage-volume). On a server that has *not* been
migrated, volumes are under the podman default `~/.local/share/containers/storage` —
which is why you should resolve paths with `podman volume inspect` (below) instead of
hardcoding them.

## What is stored where

| Data                  | Inside the container       | Host path (production)                                                    |
| --------------------- | -------------------------- | ------------------------------------------------------------------------- |
| SQLite database       | `/data/dta.db` (+ `-wal`, `-shm` sidecars) | `/srv/dta-storage/containers/storage/volumes/dta_database/_data/dta.db`   |
| Audio recordings      | `/data/audio/<user-guid>/<audio-id>.wav`   | `/srv/dta-storage/containers/storage/volumes/dta_database/_data/audio/…`  |
| App log files         | `/data/logs`               | `/srv/dta-storage/containers/storage/volumes/dta_database/_data/logs`     |
| Model weights (~16 GB)| `/weights`                 | `/srv/dta-storage/containers/storage/volumes/asa-weights/_data`           |
| Caddy access logs     | `/var/log/caddy`           | `/srv/dta-storage/containers/storage/volumes/caddy-logs/_data`            |

Each assessment row in the database stores the `audio_path` of its recording, so
`assessments.csv` + the `audio/` tree together are the full dataset.

The portable way to find any volume's host path (works on every server, staging or
production, migrated or not):

```bash
podman volume inspect -f '{{.Mountpoint}}' dta_database
```

> [!NOTE]
> Rootless podman maps the container's root user to the login user, so all of these files
> are owned by `ubuntu` on the host — `scp`/`rsync` work directly, no sudo needed.
> (Audio files are mode `600`, still readable by their owner `ubuntu`.)

## Downloading data to your machine

Run these **from your own computer** (same SSH key you use to log in).

**CSV export of all tables** (users, assessments, feedback, …):

```bash
ssh -i ~/.ssh/keyname.pem ubuntu@<server-ip> \
    'bash dta-server/scripts/export_server_data.sh --out-dir /tmp/dta_csv'   # or ~/export_server_data.sh on production
scp -i ~/.ssh/keyname.pem -r ubuntu@<server-ip>:/tmp/dta_csv ./
```

**The SQLite database itself** — do **not** just `scp dta.db` while the app is running:
recent writes may still sit in the `-wal` sidecar and you'd get a stale or torn copy.
Take a consistent snapshot first:

```bash
ssh -i ~/.ssh/keyname.pem ubuntu@<server-ip> \
    'sqlite3 "$(podman volume inspect -f "{{.Mountpoint}}" dta_database)/dta.db" ".backup /tmp/dta-snapshot.db"'
scp -i ~/.ssh/keyname.pem ubuntu@<server-ip>:/tmp/dta-snapshot.db ./
```

**Audio recordings** (incremental — re-running only fetches new files):

```bash
rsync -avz -e "ssh -i ~/.ssh/keyname.pem" \
    ubuntu@<server-ip>:"$(ssh -i ~/.ssh/keyname.pem ubuntu@<server-ip> podman volume inspect -f '{{.Mountpoint}}' dta_database)/audio/" \
    ./dta-audio/
```

(If that nested ssh feels awkward, just resolve the path once on the server and paste it.)

> [!WARNING]
> The database and the recordings are **research data about identifiable speech**.
> Download only to machines covered by the project's data management plan, and delete
> local copies when done. This is the same rule as for any DigiTala participant data.

## Backups

One-off pre-migration snapshot (old 0–5 database, all audio to that date, Caddy logs,
CSV export) is kept on production at:

```
/srv/dta-storage/backups/pre-v1.1.1/
```

There is **no automatic backup job yet** — until one exists, take a manual snapshot
(CSV export + `.backup` + audio rsync, exactly the commands above) before risky
operations and periodically for safekeeping.
