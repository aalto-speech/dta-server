#!/usr/bin/env bash
set -euo pipefail

# Resolve the database volume through podman so this works on any server, wherever the
# podman graphroot lives (production moved it to /srv/dta-storage in 2026-08).
DEFAULT_DB="$(podman volume inspect -f '{{.Mountpoint}}' dta_database 2>/dev/null || true)/dta.db"
if [[ "${DEFAULT_DB}" == "/dta.db" ]]; then
  DEFAULT_DB="/home/ubuntu/.local/share/containers/storage/volumes/dta_database/_data/dta.db"
fi
DB_PATH="${DB:-$DEFAULT_DB}"
# The recordings live next to the database in the same volume (app writes /data/audio).
DEFAULT_AUDIO_DIR="$(dirname "$DB_PATH")/audio"
AUDIO_DIR="${AUDIO_DIR:-$DEFAULT_AUDIO_DIR}"
OUT_DIR=""
TABLES=()
DEFAULT_TABLES=(users assessments feedback user_requests user_cefr_history)
WITH_AUDIO=1
PRUNE_AUDIO=0

usage() {
  cat <<'EOF'
Export DTA data: SQLite tables as CSV, plus the audio recordings they reference.

Usage:
  export_server_data.sh [options]

Options:
  --db PATH          SQLite database path.
                     Default: <dta_database volume mountpoint>/dta.db, resolved via
                     `podman volume inspect` (see docs/DATA.md)
  --audio-dir PATH   Audio directory. Default: <db directory>/audio
  --out-dir PATH     Output directory.
                     Default: /home/ubuntu/dta_export_<UTC timestamp>
  --no-audio         Export CSVs only, skip the recordings.
  --prune-audio      MOVE the recordings instead of copying: each file is deleted from
                     the server only after it has been transferred successfully.
                     Use when the server is not the archive of record and old data
                     should not linger (deletion requests, storage hygiene).
  --table NAME       Export one table. Can be repeated.
  --tables A,B,C     Export comma-separated table names.
  -h, --help         Show this help.

Default tables:
  users, assessments, feedback, user_requests, user_cefr_history

Examples:
  export_server_data.sh
  export_server_data.sh --out-dir /home/ubuntu/dta_export
  export_server_data.sh --prune-audio          # move recordings off the server
  export_server_data.sh --no-audio --table users
EOF
}

install_help() {
  cat >&2 <<'EOF'
sqlite3 is not installed.

Install options:
  Ubuntu/Debian:
    sudo apt update
    sudo apt install -y sqlite3

  Fedora/RHEL:
    sudo dnf install -y sqlite

  Alpine:
    sudo apk add sqlite

After installing, rerun:
  /home/ubuntu/export_server_data.sh
EOF
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

require_identifier() {
  local value="$1"
  if [[ ! "$value" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "Invalid table name: $value" >&2
    exit 1
  fi
}

sql_identifier() {
  local value="$1"
  require_identifier "$value"
  printf '"%s"' "$value"
}

order_clause() {
  local table="$1"
  local table_sql
  table_sql="$(sql_identifier "$table")"

  local columns
  columns="$(sqlite3 -readonly -noheader "$DB_PATH" "PRAGMA table_info($table_sql);")"

  if grep -Eq '^[0-9]+\|id\|' <<< "$columns"; then
    printf ' ORDER BY "id"'
  elif grep -Eq '^[0-9]+\|guid\|' <<< "$columns" && grep -Eq '^[0-9]+\|task_id\|' <<< "$columns"; then
    printf ' ORDER BY "guid", "task_id"'
  elif grep -Eq '^[0-9]+\|guid\|' <<< "$columns"; then
    printf ' ORDER BY "guid"'
  fi
}

export_table() {
  local table="$1"
  require_identifier "$table"

  local table_sql
  table_sql="$(sql_identifier "$table")"

  local exists
  exists="$(sqlite3 -readonly -noheader "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = '$table';")"
  if [[ "$exists" != "1" ]]; then
    local available
    available="$(sqlite3 -readonly -noheader "$DB_PATH" "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")"
    echo "Missing table: $table" >&2
    echo "Available tables:" >&2
    echo "$available" >&2
    exit 1
  fi

  local clause
  clause="$(order_clause "$table")"

  local output_path="$OUT_DIR/$table.csv"
  local query="SELECT * FROM $table_sql$clause;"
  sqlite3 -readonly -header -csv "$DB_PATH" "$query" > "$output_path"

  local count
  count="$(sqlite3 -readonly -noheader "$DB_PATH" "SELECT COUNT(*) FROM $table_sql;")"
  printf '%s (%s rows)\n' "$output_path" "$count"
  EXPORTED_TABLE_COUNT=$((EXPORTED_TABLE_COUNT + 1))
  EXPORTED_ROW_COUNT=$((EXPORTED_ROW_COUNT + count))
}

export_audio() {
  if [[ ! -d "$AUDIO_DIR" ]]; then
    echo "Audio directory not found, skipping: $AUDIO_DIR" >&2
    return
  fi

  local dest="$OUT_DIR/audio"
  mkdir -p "$dest"

  local before
  before="$(find "$AUDIO_DIR" -type f -name '*.wav' | wc -l)"

  if [[ "$PRUNE_AUDIO" -eq 1 ]]; then
    # --remove-source-files deletes each file only after rsync has transferred and
    # verified it, so an interrupted run never loses a recording.
    rsync -a --remove-source-files "$AUDIO_DIR/" "$dest/"
    # rsync leaves the now-empty per-user directories behind; the audio root stays.
    find "$AUDIO_DIR" -mindepth 1 -type d -empty -delete
  else
    rsync -a "$AUDIO_DIR/" "$dest/"
  fi

  EXPORTED_AUDIO_COUNT="$(find "$dest" -type f -name '*.wav' | wc -l)"
  local left
  left="$(find "$AUDIO_DIR" -type f -name '*.wav' 2>/dev/null | wc -l)"
  printf '%s (%s file(s), %s)\n' "$dest" "$EXPORTED_AUDIO_COUNT" "$(du -sh "$dest" | cut -f1)"
  if [[ "$PRUNE_AUDIO" -eq 1 ]]; then
    printf '  moved off the server: %s of %s file(s) removed from %s\n' \
      "$((before - left))" "$before" "$AUDIO_DIR"
  fi
}

# Data-protection cross-checks. Deleting a user (admin DELETE /users, or an approved
# deletion request) removes their database rows but NOT their WAV files, so audio can
# outlive the consent that covers it. Both checks are reported, never acted on.
report_compliance() {
  local pending
  pending="$(sqlite3 -readonly -noheader "$DB_PATH" \
    "SELECT COUNT(*) FROM user_requests WHERE type = 'delete' AND status IN ('pending', 'approved');" 2>/dev/null || echo 0)"
  if [[ "$pending" != "0" && -n "$pending" ]]; then
    echo ""
    echo "NOTE: $pending unhandled deletion request(s) in user_requests."
    echo "      Purge those GUIDs from this export and from any earlier archive copy."
  fi

  [[ -d "$AUDIO_DIR" ]] || return 0
  local orphans=0 guid
  while IFS= read -r guid; do
    [[ -n "$guid" ]] || continue
    local known
    known="$(sqlite3 -readonly -noheader "$DB_PATH" \
      "SELECT COUNT(*) FROM users WHERE guid = '$guid';" 2>/dev/null || echo 1)"
    [[ "$known" == "0" ]] && orphans=$((orphans + 1))
  done < <(find "$AUDIO_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null)

  if [[ "$orphans" -gt 0 ]]; then
    echo ""
    echo "NOTE: $orphans audio director(ies) belong to GUIDs no longer in the users table."
    echo "      Deleting a user does not delete their recordings — review and remove these."
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      DB_PATH="${2:?--db requires a path}"
      # Keep audio next to the database unless it was set explicitly.
      if [[ "$AUDIO_DIR" == "$DEFAULT_AUDIO_DIR" ]]; then
        AUDIO_DIR="$(dirname "$DB_PATH")/audio"
        DEFAULT_AUDIO_DIR="$AUDIO_DIR"
      fi
      shift 2
      ;;
    --audio-dir)
      AUDIO_DIR="${2:?--audio-dir requires a path}"
      shift 2
      ;;
    --no-audio)
      WITH_AUDIO=0
      shift
      ;;
    --prune-audio)
      PRUNE_AUDIO=1
      shift
      ;;
    --out-dir)
      OUT_DIR="${2:?--out-dir requires a path}"
      shift 2
      ;;
    --table)
      TABLES+=("${2:?--table requires a table name}")
      shift 2
      ;;
    --tables)
      IFS=',' read -r -a PARTS <<< "${2:?--tables requires table names}"
      for PART in "${PARTS[@]}"; do
        TABLES+=("$(trim "$PART")")
      done
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v sqlite3 >/dev/null 2>&1; then
  install_help
  exit 127
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "Database not found: $DB_PATH" >&2
  exit 1
fi

if [[ "$WITH_AUDIO" -eq 1 ]] && ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is not installed (needed for the audio export)." >&2
  echo "Install it with: sudo apt install -y rsync    — or rerun with --no-audio" >&2
  exit 127
fi

if [[ "$PRUNE_AUDIO" -eq 1 && "$WITH_AUDIO" -eq 0 ]]; then
  echo "--prune-audio cannot be combined with --no-audio" >&2
  exit 2
fi

if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="/home/ubuntu/dta_export_$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$OUT_DIR"

if [[ ${#TABLES[@]} -eq 0 ]]; then
  TABLES=("${DEFAULT_TABLES[@]}")
fi

for TABLE in "${TABLES[@]}"; do
  require_identifier "$TABLE"
done

echo "Exporting from: $DB_PATH"
[[ "$WITH_AUDIO" -eq 1 ]] && echo "Audio from:     $AUDIO_DIR$([[ $PRUNE_AUDIO -eq 1 ]] && echo '  (MOVE: files are removed after transfer)')"
echo "Writing to:     $OUT_DIR"
echo ""

EXPORTED_TABLE_COUNT=0
EXPORTED_ROW_COUNT=0
EXPORTED_AUDIO_COUNT=0
for TABLE in "${TABLES[@]}"; do
  export_table "$TABLE"
done

if [[ "$WITH_AUDIO" -eq 1 ]]; then
  export_audio
fi

echo ""
echo "Done. Exported $EXPORTED_TABLE_COUNT table(s), $EXPORTED_ROW_COUNT row(s), $EXPORTED_AUDIO_COUNT recording(s)."

report_compliance
