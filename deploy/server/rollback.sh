#!/usr/bin/env bash
# Roll Slate back to an earlier git ref (tag or commit). Run as the service user:
#   bash rollback.sh <ref>
# Rebuilds and restarts the container from that ref, and reports success only once /healthz
# serves that commit. Never touches $SLATE_HOME/data (default /opt/slate/data). Before anything
# changes it reads the database's migration revision: if <ref> does not know it, the old code
# could not start (it would restart in a loop), so it stops and says how to restore a backup
# from before that migration (RUNBOOK.md, "Rollback past a migration"). Otherwise it takes a
# `pre-rollback-<stamp>.db` backup of a running container first.
# SLATE_WAIT_SECONDS (default 60) is how long to wait for the new build to answer.
set -euo pipefail

# In main() so bash has parsed everything before git checkout replaces this file.
main() {
  local home="${SLATE_HOME:-/opt/slate}"
  local repo="$home/repo"
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ $# -ne 1 ]]; then
    echo "usage: rollback.sh <ref>   (a tag, commit or branch from: git -C $repo log --oneline)" >&2
    exit 2
  fi
  local ref="$1"
  local health=http://127.0.0.1:18448/healthz

  echo "==> fetching"
  git -C "$repo" fetch --prune --tags origin
  local target
  if ! target="$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}")"; then
    echo "unknown ref: $ref" >&2
    exit 2
  fi

  # The database file on the host; .env may name another file under /data.
  local name=""
  [[ -f "$home/.env" ]] && name="$(sed -n 's|^SLATE_DATABASE_PATH=/data/||p' "$home/.env" | tail -n 1)"
  local db="$home/data/${name:-slate.db}"
  local rev=""
  if [[ -f "$db" ]]; then
    rev="$(SLATE_DATABASE_PATH="$db" python3 - --revision < "$here/backup.py")"
  fi
  if [[ -n "$rev" ]] && ! git -C "$repo" grep -q -F -e "$rev" "$target" -- alembic/versions; then
    {
      echo "The database is at migration $rev, which $ref does not have: its code would not"
      echo "start against this database. Nothing was changed. To go back that far, restore a"
      echo "backup taken before that migration (the pre-deploy-*.db before it, in"
      echo "$home/backups), then run this again: RUNBOOK.md, \"Rollback past a migration\"."
    } >&2
    exit 1
  fi

  if [[ "$(docker inspect -f '{{.State.Running}}' slate 2>/dev/null)" == true ]]; then
    echo "==> backing up the live database before the rollback"
    SLATE_HOME="$home" bash "$here/backup.sh" "pre-rollback-$(date +%F-%H%M).db"
  else
    echo "==> slate is not running: no pre-rollback backup (the newest is in $home/backups)"
  fi

  echo "==> checking out $ref"
  git -C "$repo" checkout --quiet --detach "$target"
  git -C "$repo" log -1 --format='    %h %s (%ci)'

  echo "==> rebuilding and restarting slate.service"
  if systemctl --user is-active --quiet slate.service; then
    systemctl --user reload slate.service
  else
    systemctl --user start slate.service
  fi

  wait_for_commit "$health" "$target" "${SLATE_WAIT_SECONDS:-60}"
  echo "==> rolled back to $ref"
}

# Wait until /healthz answers with the commit just checked out (an old container still
# answering, or a build that fails to start, is not success).
wait_for_commit() {
  local health="$1" want="$2" seconds="$3" body="" commit=""
  echo "==> waiting up to $seconds s for $health to serve ${want:0:12}"
  local tries=$(( seconds / 2 > 0 ? seconds / 2 : 1 ))
  for _ in $(seq "$tries"); do
    body="$(curl -fsS "$health" 2>/dev/null)" || body=""
    commit="$(sed -n 's/.*"commit": *"\([0-9a-f]\{7,40\}\)".*/\1/p' <<< "$body")"
    if [[ -n "$commit" && "$want" == "$commit"* ]]; then
      echo "$body"
      return 0
    fi
    sleep 2
  done
  if [[ -n "$body" ]]; then
    echo "Slate answers $health but serves ${commit:-an unknown commit}, not ${want:0:12}; see: docker logs slate" >&2
  else
    echo "Slate did not answer $health within $seconds s; see: docker logs slate" >&2
  fi
  exit 1
}

main "$@"
