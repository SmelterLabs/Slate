# Slate on a Linux host — runbook

A single-host Docker deployment: one container built from a git checkout on the host, run by
systemd **user** units of a dedicated service user, with nightly online backups. For a quick
local try-out use the root `compose.yaml` instead; this pack is for a server you keep.

Placeholders used below: service user `slate`, install dir `/opt/slate`, board address
`https://slate.example.com`. Replace them with yours.

What ends up where (`SLATE_HOME` defaults to `/opt/slate`):

| Thing | Place |
|---|---|
| Git checkout | `$SLATE_HOME/repo` |
| Database (WAL) | `$SLATE_HOME/data/slate.db` → `/data/slate.db` in the container |
| Nightly backups, 30 days | `$SLATE_HOME/backups/slate-YYYY-MM-DD.db` (not mounted into the container) |
| Last backup's outcome (the Health page reads it) | `$SLATE_HOME/data/backup-status.json` |
| Settings, mode 600, never committed | `$SLATE_HOME/.env` (template: `deploy/server/env.example`) |
| User units | `~/.config/systemd/user/slate.service`, `slate-backup.service`, `slate-backup.timer` |
| Container / compose project | `slate` / `slate` (always `docker compose -p slate`) |
| Loopback port | `127.0.0.1:18448` |
| Public address | your reverse proxy, e.g. `https://slate.example.com` |

**Another install dir.** Every script reads `SLATE_HOME` (default `/opt/slate`); `deploy.sh`
installs the units with `/opt/slate` rewritten to it. To keep an existing layout, put
`export SLATE_HOME=/srv/slate` (or wherever it lives) in the service user's `~/.profile` and
run the scripts from a login shell.

## 1. Host and service user (once)

Requirements: Linux with systemd, Docker Engine with the compose plugin (`docker compose`),
`git`, `curl`, `python3`. Node is not needed on the host; it only exists inside the image build.

```bash
sudo useradd --create-home --shell /bin/bash slate
sudo usermod -aG docker slate
sudo loginctl enable-linger slate        # user units keep running without a login session
sudo install -d -m 0750 -o slate -g slate /opt/slate
```

Everything after this runs as `slate` (`sudo -iu slate`).

## 2. Settings and first deploy

```bash
mkdir -p /opt/slate/data /opt/slate/backups
git clone https://github.com/SmelterLabs/Slate.git /opt/slate/repo
cp /opt/slate/repo/deploy/server/env.example /opt/slate/.env
chmod 600 /opt/slate/.env
key=$(python3 -c "import secrets;print(secrets.token_hex(32))")
sed -i "s|^SLATE_SECRET_KEY=.*|SLATE_SECRET_KEY=$key|" /opt/slate/.env
unset key
grep -c '^SLATE_SECRET_KEY=[0-9a-f]\{64\}$' /opt/slate/.env   # prints 1; never cat the file
```

For a private fork, clone over SSH with a **read-only** deploy key instead: make a key
(`ssh-keygen -t ed25519 -f ~/.ssh/slate-deploy -N ""`), add `~/.ssh/slate-deploy.pub` as a
read-only deploy key on the fork, give `~/.ssh/config` a `Host github.com-slate` entry
(`HostName github.com`, `User git`, `IdentityFile ~/.ssh/slate-deploy`, `IdentitiesOnly yes`),
and clone `git@github.com-slate:<you>/<fork>.git`.

Edit `/opt/slate/.env` for the rest (at least `SLATE_TIMEZONE` and `SLATE_PUBLIC_URL`), then:

```bash
bash /opt/slate/repo/deploy/server/deploy.sh main
```

`deploy.sh` is idempotent. It creates the folders, fetches and checks out the ref (a first run
without a checkout clones `SLATE_REPO_URL`), installs the three user units, runs
`systemctl --user daemon-reload` and `enable --now slate.service slate-backup.timer` (the first
start builds the image: a few minutes), waits up to 60 s for `http://127.0.0.1:18448/healthz`
and prints the health JSON. Expected health (the values here are placeholders; yours differ):

```json
{"ok": true, "version": "0.1.0", "rev": "42", "commit": "abc1234", "db": "ok", "scheduler_last_tick_age_s": 12.3, "webhooks_enabled": true, "board": "/assets/index-<hash>.js", "min_runner_protocol": 3}
```

`scheduler_last_tick_age_s` should stay under ~2× `SLATE_SCHEDULER_INTERVAL_SECONDS` (60 s).
`webhooks_enabled` is true once the secret key is set. `board` names the board bundle being
served; open boards reload when it changes.

Back up the secret key now: losing it makes every stored webhook secret unreadable. Store it
in your secrets manager, piping
`grep '^SLATE_SECRET_KEY=' /opt/slate/.env | cut -d= -f2` straight into its store command;
never echo it.

## 3. Put it behind https

The container listens on loopback only. Put a TLS-terminating reverse proxy in front of
`http://127.0.0.1:18448` and point `SLATE_PUBLIC_URL` at the address it serves. Any proxy
works; with Caddy, for example:

```
slate.example.com {
    reverse_proxy 127.0.0.1:18448
}
```

The proxy must pass `X-Forwarded-Proto` and `X-Forwarded-For`. `compose.yml` trusts them from
loopback and Docker's `172.16.0.0/12` networks only, and takes the right-most address no
trusted hop added, so a client cannot choose the address the rate limits see. Caddy sets the
header itself; with nginx use `proxy_set_header X-Forwarded-For $remote_addr;` (or
`$proxy_add_x_forwarded_for`, which is also safe with this list). A proxy that reaches the
container from another network needs its subnet added to `FORWARDED_ALLOW_IPS` in
`compose.yml` (`docker network inspect slate_default` shows the compose network's subnet; never
use `*`, which trusts every entry a client sends). Server-sent events (`/api/v1/events/stream`) must not be
buffered: with nginx, set `proxy_buffering off;` for that location.

### Optional: expose on your tailnet instead

To keep the board reachable only from your Tailscale tailnet, skip the proxy and let
`deploy.sh` set up Tailscale Serve: run it with `SLATE_TAILSCALE_PORT=8448` (exported in
`~/.profile`, so updates keep it). It adds
`tailscale serve --bg --https=8448 http://127.0.0.1:18448` unless that port is already served,
and the board is then at the host's tailnet name on port 8448 (`tailscale serve status` prints the address). If `tailscale serve`
says permission denied, run once `sudo tailscale set --operator=slate` and re-run `deploy.sh`.

## 4. Accounts (first run only)

Bootstrap works only while there are no accounts (afterwards it returns 410 Gone). Request
body: `{"handle": "...", "display_name": "..."}`. Response:
`{"account": {...}, "token_id": "...", "scopes": ["admin"], "token": "slate_..."}`. The
`token` appears in this response and never again; capture it straight into a mode-600 file,
do not print it.

```bash
umask 077
curl -fsS -X POST http://127.0.0.1:18448/api/v1/bootstrap \
  -H 'Content-Type: application/json' \
  -d '{"handle": "alex", "display_name": "Alex"}' > ~/slate-bootstrap.json
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/slate-bootstrap.json')));print(d['account']['id'], d['scopes'])"
ADMIN=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/slate-bootstrap.json')))['token'])")
```

Then an agent account, for example `my-agent` (body `{"handle", "display_name", "kind": "person"|"agent"}`),
and its token (body `{"scopes": [...], "label": "..."}`, scopes from `read`, `write`, `admin`):

```bash
AGENT_ID=$(curl -fsS -X POST http://127.0.0.1:18448/api/v1/accounts \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"handle": "my-agent", "display_name": "My Agent", "kind": "agent"}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
curl -fsS -X POST "http://127.0.0.1:18448/api/v1/accounts/$AGENT_ID/tokens" \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"scopes": ["read", "write"], "label": "my-agent"}' > ~/slate-agent-token.json
unset ADMIN
```

Move both tokens into your secrets manager, then
`shred -u ~/slate-bootstrap.json ~/slate-agent-token.json`. Hand each token to its owner
privately, never in a repo or a chat log.

## 5. Verify

Health through your public address:

```bash
curl -fsS https://slate.example.com/healthz
```

Board: open `https://slate.example.com/` in a browser and sign in with the admin token.

One MCP call round-trip (streamable HTTP, stateless: a single POST works without
`initialize`), with `SLATE_TOKEN` set to the agent's token:

```bash
curl -fsS https://slate.example.com/mcp \
  -H "Authorization: Bearer $SLATE_TOKEN" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"slate_account","arguments":{"action":"me"}}}'
```

Pass: HTTP 200 and `"isError":false` with `"handle":"my-agent"` in the result text.

## 6. Day to day

**Update** to the newest `main` (or a tag): `bash /opt/slate/repo/deploy/server/deploy.sh main`
(or `... deploy.sh v0.2.0`). It refuses to run over local edits in the checkout. Migrations
run automatically at container start.

**Before a deploy that changes what webhooks may reach** (any time
`SLATE_WEBHOOK_PRIVATE_TARGETS` is changed): `docker exec slate slate check-targets`. It lists
every webhook and automation that points at a private address (loopback, LAN, tailnet, bare
names) and exits 1 if there is any; those stop delivering unless `.env` has
`SLATE_WEBHOOK_PRIVATE_TARGETS=true`. Run it against the live container before the update,
read the list, then decide: set the flag, or move those receivers to a public https address.

**Before a deploy that adds a migration:** `deploy.sh` takes a `pre-deploy-<stamp>.db` backup
itself before restarting a running container (same online-backup script; pruned with the
nightly ones after 30 days). For a large or precious database, also rehearse the migration on
a copy: copy the newest file from `/opt/slate/backups/` elsewhere and run `alembic upgrade head` against
it with `SLATE_DATABASE_PATH`. An empty test database proves nothing here: a rebuild of a table
other tables point at can pass every test and then fail on a real file. If a deploy leaves the
container restarting, `docker logs slate` shows the migration error; stop the service and use
the rollback below.

**Rollback** to an earlier ref: `bash /opt/slate/repo/deploy/server/rollback.sh <tag-or-commit>`.
Data is never touched. If the bad release added a database migration, the old code may not
start against the newer schema: stop the service, restore the last backup taken before that
deploy (into `/opt/slate/data/slate.db` after moving the current file and its `-wal`/`-shm`
aside), then roll back.

**Logs:** `docker logs slate --since 1h` (rotated at 10 MB × 5). Unit logs:
`journalctl --user -u slate.service -u slate-backup.service`.

**Status:** `systemctl --user status slate.service`, `systemctl --user list-timers slate-backup.timer`,
`docker ps --filter name=slate`, `docker compose -p slate ps` (from any directory; `-p slate` always).

**Restart / stop:** `systemctl --user reload slate.service` (rebuild + recreate if changed) /
`systemctl --user stop slate.service` (compose down; up to 20 s for in-flight work).

**Backup now:** `systemctl --user start slate-backup.service`, then
`journalctl --user -u slate-backup.service -n 5`. Backups use SQLite's online backup API from
inside the container (never a file copy of the live WAL database). The container checks the
copy (integrity check, a migration revision and the `items` table, so a missing or empty
database is an error, never an "ok" backup) and streams it out; `backup.sh` checks its SHA-256
on arrival, renames it into `/opt/slate/backups/` (mode 700, files 600) and prunes anything
older than 30 days. The container never mounts that folder, so neither the app nor a mistake
in `data/` can reach the backups. The timer fires at 03:30 in the zone named on the
`OnCalendar` line of `slate-backup.timer`; edit that line for your zone.

**A failing backup shows on the Health page.** Every run records its outcome in
`data/backup-status.json`; the Health page's Backups line fails when the last run failed or
the last good backup is more than 36 hours old, and `GET /health` then answers 503, so an
uptime monitor on `/health` hears about it.

`/opt/slate/backups/` is still on the same disk as the database. Mount another disk there, or
copy the folder off the host (encrypted) with whatever you use for backups.

## 7. Backup restore drill (monthly, and before trusting a new backup setup)

Restore the newest backup to a temp path and query it; the live database is not touched:

```bash
latest=$(ls -1 /opt/slate/backups/slate-*.db | tail -n 1)
tmp=$(mktemp -d)
cp "$latest" "$tmp/restore.db"
python3 -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);print(c.execute('PRAGMA integrity_check').fetchone()[0], c.execute('SELECT count(*) FROM items').fetchone()[0])" "$tmp/restore.db"
rm -r "$tmp"
```

Pass: `ok <n>` where `<n>` is close to the live count (a backup is at most a day old). A
backup file is a closed, self-contained SQLite file, so a plain `cp` of it is fine; only the
live database needs the backup API.

## Optional: push notifications to your own broker

`deploy/server/slate-push.service` runs `integrations/push/bridge.py`, which posts one
account's notifications to a push-notification webhook. It is not installed by `deploy.sh`;
see `integrations/push/README.md`.

## AI add (optional)

`POST /items/ai` asks an Ollama server to fill in a task described in plain words. With
Ollama on the same host, set `SLATE_AI_URL=http://host.docker.internal:11434` (and
`SLATE_AI_MODEL` to a model you pulled) in `/opt/slate/.env` and restart. The container
reaches the host through `extra_hosts: host-gateway`. If the host has a firewall, allow the
Ollama port from the slate compose network only: find its subnet with
`docker network inspect slate_default --format '{{(index .IPAM.Config 0).Subnet}}'`, then for
example `sudo ufw allow from <subnet> to any port 11434 proto tcp comment 'slate -> ollama'`.
Check: `docker exec slate python -c "import urllib.request;print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').read()[:80])"`.
Ollama down = AI add leaves cards as typed; nothing else in Slate depends on it. Off: blank
`SLATE_AI_URL` and restart.

## Gotchas

- User units cannot order after the system `docker.service`; `slate.service` waits up to 2
  minutes for `docker info` in `ExecStartPre`. The container also has `restart: unless-stopped`,
  so Docker brings it back on its own after a reboot.
- The service user must be in the `docker` group and have lingering enabled
  (`loginctl enable-linger`), or the user units stop at logout and never start at boot.
- The image is built on the host from the checkout (`slate:local`); nothing is pulled from a
  registry.
- `$SLATE_HOME/data` must exist before the first `up`, or Docker creates it owned by root;
  `deploy.sh` creates it (mode 700, as are `backups/` and the `.env`'s 600).
- The container runs as the service user (`slate.service` passes its uid and gid), with every
  capability dropped and `no-new-privileges`. Images before this ran as root; the first
  `deploy.sh` after the change hands the root-owned files in `data/` to the service user, once.
- Older installs kept backups in `data/backups/`; `deploy.sh` moves them to `backups/` once.
