#!/usr/bin/env bash
# Deploy or update Slate on a Linux host. Run as the service user:  bash deploy.sh [ref]
# (ref defaults to main). Idempotent: safe to re-run; it never touches $SLATE_HOME/data except
# to create it.
#
# Environment (all optional once the checkout exists):
#   SLATE_HOME            install dir (default /opt/slate): repo/, data/, .env
#   SLATE_REPO_URL        git URL to clone on the first run (later runs use the checkout's origin)
#   SLATE_TAILSCALE_PORT  if set (e.g. 8448), also expose the board on the tailnet with
#                         `tailscale serve --https=<port>` (RUNBOOK.md, "Expose on your tailnet")
set -euo pipefail

# Everything lives in main() so bash has parsed the whole script before git checkout can
# replace this file underneath it.
main() {
  local ref="${1:-main}"
  local home="${SLATE_HOME:-/opt/slate}"
  local repo="$home/repo"
  local units="$HOME/.config/systemd/user"
  local health=http://127.0.0.1:18448/healthz
  local ts_port="${SLATE_TAILSCALE_PORT:-}"

  step() { echo; echo "==> $*"; }

  step "creating $home/{repo,data/backups}"
  mkdir -p "$home/repo" "$home/data/backups"

  if [[ ! -f "$home/.env" ]]; then
    echo "missing $home/.env (copy deploy/server/env.example, fill SLATE_SECRET_KEY, chmod 600)" >&2
    exit 1
  fi
  chmod 600 "$home/.env"

  if [[ -d "$repo/.git" ]]; then
    step "fetching $(git -C "$repo" remote get-url origin)"
    if [[ -n "$(git -C "$repo" status --porcelain)" ]]; then
      echo "local changes in $repo; refusing to overwrite them" >&2
      git -C "$repo" status --short >&2
      exit 1
    fi
    git -C "$repo" fetch --prune --tags origin
  else
    if [[ -z "${SLATE_REPO_URL:-}" ]]; then
      echo "no checkout in $repo yet: set SLATE_REPO_URL to the git URL to clone" >&2
      exit 1
    fi
    step "cloning $SLATE_REPO_URL into $repo"
    git clone "$SLATE_REPO_URL" "$repo"
  fi

  step "checking out $ref"
  # A branch name deploys the remote tip; a tag or commit deploys exactly that.
  if git -C "$repo" rev-parse --verify --quiet "refs/remotes/origin/$ref" >/dev/null; then
    git -C "$repo" checkout --quiet --detach "origin/$ref"
  else
    git -C "$repo" checkout --quiet --detach "$ref"
  fi
  git -C "$repo" log -1 --format='    %h %s (%ci)'

  step "installing user units into $units (install dir $home)"
  mkdir -p "$units"
  # The units in the repo name the default /opt/slate; install them for this SLATE_HOME.
  local unit
  for unit in slate.service slate-backup.service slate-backup.timer; do
    sed "s|/opt/slate|$home|g" "$repo/deploy/server/$unit" > "$units/$unit"
  done
  systemctl --user daemon-reload

  local was_active=no
  systemctl --user is-active --quiet slate.service && was_active=yes

  # The container runs `slate migrate` at start, so a backup of the live file comes first:
  # a migration that drops tables must have a copy to roll back to.
  if [[ "$was_active" == yes ]] && docker ps --format '{{.Names}}' | grep -qx slate; then
    step "backing up the live database before the restart (migrations run at container start)"
    SLATE_HOME="$home" bash "$repo/deploy/server/backup.sh" "pre-deploy-$(date +%F-%H%M).db"
  fi

  step "enabling and starting slate.service and slate-backup.timer (builds the image)"
  systemctl --user enable --now slate.service slate-backup.timer
  if [[ "$was_active" == yes ]]; then
    step "slate.service was already running: reloading to rebuild and recreate the container"
    systemctl --user reload slate.service
  fi

  step "waiting up to 60 s for $health"
  local body=""
  for _ in $(seq 30); do
    if body="$(curl -fsS "$health" 2>/dev/null)"; then break; fi
    body=""
    sleep 2
  done
  if [[ -z "$body" ]]; then
    echo "Slate did not answer $health within 60 s; see: docker logs slate" >&2
    exit 1
  fi
  echo "$body"

  if [[ -n "$ts_port" ]]; then
    step "Tailscale Serve: https on $ts_port -> 127.0.0.1:18448"
    if tailscale serve status 2>/dev/null | grep -q ":$ts_port"; then
      echo "    already serving $ts_port; leaving it alone"
    else
      tailscale serve --bg --https="$ts_port" http://127.0.0.1:18448
    fi
    tailscale serve status
  fi

  step "done: Slate $(git -C "$repo" describe --always --tags) is live"
}

main "$@"
