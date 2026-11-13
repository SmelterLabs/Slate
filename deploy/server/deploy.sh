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
#   SLATE_WAIT_SECONDS    how long to wait for /healthz to serve the new commit (default 60)
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

  step "creating $home/{repo,data,backups}"
  mkdir -p "$home/repo" "$home/data" "$home/backups"
  # Backups live beside data/, not in it: the container mounts data/ and never sees them.
  if [[ -d "$home/data/backups" ]]; then
    step "moving old backups from $home/data/backups to $home/backups"
    find "$home/data/backups" -maxdepth 1 -type f -name '*.db' -exec mv -n {} "$home/backups/" \;
    rmdir "$home/data/backups" 2>/dev/null || echo "    kept $home/data/backups: it holds other files"
  fi

  if [[ ! -f "$home/.env" ]]; then
    echo "missing $home/.env (copy deploy/server/env.example, fill SLATE_SECRET_KEY, chmod 600)" >&2
    exit 1
  fi
  chmod 600 "$home/.env"
  chmod 700 "$home/data" "$home/backups"

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
  if [[ "$was_active" == yes && "$(docker inspect -f '{{.State.Running}}' slate 2>/dev/null)" == true ]]; then
    step "backing up the live database before the restart (migrations run at container start)"
    SLATE_HOME="$home" bash "$repo/deploy/server/backup.sh" "pre-deploy-$(date +%F-%H%M).db"
  fi

  # The container runs as this user. An older image ran as root and left root-owned files in
  # data/; hand them over once (docker access is root on this host anyway), before it starts.
  if [[ -n "$(find "$home/data" ! -user "$(id -u)" -print -quit)" ]]; then
    step "giving $home/data to $(id -un) (files an older container wrote as root)"
    docker run --rm --user 0:0 --network none -v "$home/data:/data" --entrypoint chown \
      slate:local -R "$(id -u):$(id -g)" /data
  fi

  step "enabling and starting slate.service and slate-backup.timer (builds the image)"
  systemctl --user enable --now slate.service slate-backup.timer
  if [[ "$was_active" == yes ]]; then
    step "slate.service was already running: reloading to rebuild and recreate the container"
    systemctl --user reload slate.service
  fi

  wait_for_commit "$health" "$(git -C "$repo" rev-parse HEAD)" "${SLATE_WAIT_SECONDS:-60}"

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

# Wait until /healthz answers with the commit just checked out (an old container still
# answering, or a build that fails to start, is not success).
wait_for_commit() {
  local health="$1" want="$2" seconds="$3" body="" commit=""
  echo
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
