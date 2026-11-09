#!/usr/bin/env bash
# Back up the live Slate database with SQLite's online backup API (never a file copy: the
# database runs in WAL mode), then prune backups older than 30 days. Run by slate-backup.service
# nightly, and by deploy.sh before it restarts the container (`backup.sh pre-deploy-<stamp>.db`).
# Backups land in $SLATE_HOME/data/backups (default /opt/slate/data/backups).
set -euo pipefail

main() {
  local backups="${SLATE_HOME:-/opt/slate}/data/backups"
  local name="${1:-slate-$(date +%F).db}"   # date computed on the host, passed into the container

  echo "==> backing up /data/slate.db to /data/backups/$name"
  # Write to .tmp then rename, so a half-written file never looks like a good backup.
  docker exec slate python -c '
import os, sqlite3, sys
dst_path = sys.argv[1]
tmp = dst_path + ".tmp"
src = sqlite3.connect("/data/slate.db")
dst = sqlite3.connect(tmp)
with dst:
    src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()
src.close()
if ok != "ok":
    os.remove(tmp)
    sys.exit("integrity_check failed: " + ok)
os.replace(tmp, dst_path)
print("backup ok:", dst_path, os.path.getsize(dst_path), "bytes")
' "/data/backups/$name"

  echo "==> pruning backups older than 30 days in $backups"
  find "$backups" -maxdepth 1 \( -name 'slate-*.db' -o -name 'pre-deploy-*.db' \) -type f -mtime +30 -print -delete
}

main "$@"
