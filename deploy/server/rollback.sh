#!/usr/bin/env bash
# Roll Slate back to an earlier git ref (tag or commit). Run as the service user:
#   bash rollback.sh <ref>
# Rebuilds and restarts the container from that ref. Never touches $SLATE_HOME/data (default
# /opt/slate/data); a ref older than the newest database migration needs a backup restore too
# (see RUNBOOK.md).
set -euo pipefail

# In main() so bash has parsed everything before git checkout replaces this file.
main() {
  local home="${SLATE_HOME:-/opt/slate}"
  local repo="$home/repo"
  if [[ $# -ne 1 ]]; then
    echo "usage: rollback.sh <ref>   (a tag, commit or branch from: git -C $repo log --oneline)" >&2
    exit 2
  fi
  local ref="$1"
  local health=http://127.0.0.1:18448/healthz

  echo "==> fetching"
  git -C "$repo" fetch --prune --tags origin

  echo "==> checking out $ref"
  git -C "$repo" checkout --quiet --detach "$ref"
  git -C "$repo" log -1 --format='    %h %s (%ci)'

  echo "==> rebuilding and restarting slate.service"
  if systemctl --user is-active --quiet slate.service; then
    systemctl --user reload slate.service
  else
    systemctl --user start slate.service
  fi

  echo "==> waiting up to 60 s for $health"
  for _ in $(seq 30); do
    if curl -fsS "$health"; then echo; echo "==> rolled back to $ref"; return 0; fi
    sleep 2
  done
  echo "Slate did not answer $health within 60 s; see: docker logs slate" >&2
  exit 1
}

main "$@"
