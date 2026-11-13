#!/usr/bin/env bash
# Back up the live Slate database with SQLite's online backup API (never a file copy: the
# database runs in WAL mode), then prune backups older than 30 days. Run by slate-backup.service
# nightly, by deploy.sh before it restarts the container (`backup.sh pre-deploy-<stamp>.db`) and
# by rollback.sh (`pre-rollback-<stamp>.db`).
#
# backup.py runs inside the container and streams the checked copy out; this script writes it
# to $SLATE_HOME/backups (default /opt/slate/backups), a folder the container does not mount, so
# neither the app nor a `rm -r data` can reach the backups. Every run, good or bad, is recorded
# in data/backup-status.json, which the Health page reports.
set -euo pipefail

main() {
  local home="${SLATE_HOME:-/opt/slate}"
  local backups="$home/backups"
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local name="${1:-slate-$(date +%F).db}"   # dated on the host
  local tmp="$backups/.$name.tmp" log="$backups/.$name.log"

  umask 077
  mkdir -p "$backups"
  chmod 700 "$backups"

  # Record the outcome where the Health page sees it; failing to record is only a warning.
  record() {
    docker exec -i slate python - --record "$@" < "$here/backup.py" \
      || echo "warning: could not record the backup status in the container" >&2
  }
  fail() {
    rm -f "$tmp" "$log"
    echo "backup failed: $1" >&2
    record failed "$1"
    exit 1
  }

  echo "==> backing up the live database to $backups/$name"
  # Write to .tmp then rename, so a half-written file never looks like a good backup.
  if ! docker exec -i slate python - < "$here/backup.py" > "$tmp" 2> "$log"; then
    cat "$log" >&2
    fail "$(tail -n 1 "$log")"
  fi
  local expected actual
  expected="$(sed -n 's/^sha256 //p' "$log" | tail -n 1 | tr -d '\r')"
  actual="$(sha256sum "$tmp" | cut -d' ' -f1) $(wc -c < "$tmp" | tr -d ' ')"
  if [[ -z "$expected" || "$expected" != "$actual" ]]; then
    fail "the copy that arrived ($actual) is not the one the container sent (${expected:-nothing})"
  fi
  mv -f "$tmp" "$backups/$name"
  rm -f "$log"
  echo "backup ok: $backups/$name ${actual#* } bytes"
  record ok "$name ${actual#* } bytes"

  echo "==> pruning backups older than 30 days in $backups"
  find "$backups" -maxdepth 1 -type f -mtime +30 \
    \( -name 'slate-*.db' -o -name 'pre-deploy-*.db' -o -name 'pre-rollback-*.db' \) -print -delete
}

main "$@"
