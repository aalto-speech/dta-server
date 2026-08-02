#!/usr/bin/env bash
set -euo pipefail

# Resolve the database volume through podman so this works on any server, wherever the
# podman graphroot lives (production moved it to /srv/dta-storage in 2026-08).
DEFAULT_DB="$(podman volume inspect -f '{{.Mountpoint}}' dta_database 2>/dev/null || true)/dta.db"
if [[ "${DEFAULT_DB}" == "/dta.db" ]]; then
  DEFAULT_DB="/home/ubuntu/.local/share/containers/storage/volumes/dta_database/_data/dta.db"
fi
DB_PATH="${DB:-$DEFAULT_DB}"
OUT_DIR=""
TABLES=()
DEFAULT_TABLES=(users assessments feedback user_requests user_cefr_history)

usage() {
  cat <<'EOF'
Export DTA SQLite tables to CSV files.

Usage:
  /home/ubuntu/export_server_data.sh [options]

Options:
  --db PATH          SQLite database path.
                     Default: <dta_database volume mountpoint>/dta.db, resolved via
                     `podman volume inspect` (see docs/DATA.md)
  --out-dir PATH     Output directory.
                     Default: /home/ubuntu/dta_csv_export_<UTC timestamp>
  --table NAME       Export one table. Can be repeated.
  --tables A,B,C     Export comma-separated table names.
  -h, --help         Show this help.

Default tables:
  users, assessments, feedback, user_requests, user_cefr_history

Examples:
  /home/ubuntu/export_server_data.sh
  /home/ubuntu/export_server_data.sh --out-dir /home/ubuntu/dta_csv_export
  /home/ubuntu/export_server_data.sh --table users --table assessments
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      DB_PATH="${2:?--db requires a path}"
      shift 2
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

if [[ -z "$OUT_DIR" ]]; then
  OUT_DIR="/home/ubuntu/dta_csv_export_$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$OUT_DIR"

if [[ ${#TABLES[@]} -eq 0 ]]; then
  TABLES=("${DEFAULT_TABLES[@]}")
fi

for TABLE in "${TABLES[@]}"; do
  require_identifier "$TABLE"
done

echo "Exporting from: $DB_PATH"
echo "Writing CSVs to: $OUT_DIR"
echo ""

EXPORTED_TABLE_COUNT=0
EXPORTED_ROW_COUNT=0
for TABLE in "${TABLES[@]}"; do
  export_table "$TABLE"
done

echo ""
echo "Done. Exported $EXPORTED_TABLE_COUNT table(s), $EXPORTED_ROW_COUNT row(s)."
