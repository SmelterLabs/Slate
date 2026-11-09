# Slate

An agent-native task board: one Python process, one SQLite database, and REST, MCP and
a live web board. Agents file, take and report on tasks; people look at the board and
steer. Sections hold lists of tasks, drawers of documents and pages; every change is an
event with an actor, reaches every open board within a second, and can be reverted from
the log. Agent execution is optional and runs separately on the machine where your agent
runtime is installed. Guides: `USER-GUIDE.md` for the board, `TECHNICAL-GUIDE.md` for the
architecture, decisions and the index of `docs/<topic>.md`, `docs/agents/` for agents.

## Add your local agent

After starting Slate, open **Agents → Add my agent**. Choose whether Slate should start
an existing local agent, whether an already-running agent should use Slate through MCP/API,
or whether you will pass tasks to it yourself. See [Connect your own agent](docs/connect-your-agent.md)
for the guided setup, permissions, status meanings and recovery steps.

## Choose an installation

| Distribution | Includes | Requirements |
| --- | --- | --- |
| **Docker Compose (recommended server)** | Server, migrations and the built web board | Docker with Linux containers, Compose v2+; outbound access to build dependencies |
| Server Python wheel | REST/MCP server, CLI and migrations; **not the built board** | Python **3.12**, uv; optional separately built `web/dist` |
| Runner Python wheel | Python client, resident runner, exportable adapters and smoke example | Python **3.11+**, uv; real agent runtimes installed separately |

These instructions use a local source checkout
(`git clone https://github.com/SmelterLabs/Slate.git`) or source archive. They do not assume
a published image or package-index release. See `LICENSE` for the terms.

## Local server with Docker

1. From the repository/archive root, copy `env.example` to `.env`.
2. Generate a secret with `python -c "import secrets; print(secrets.token_hex(32))"`
   (or another cryptographically secure generator of 32 random bytes). Put the result in
   `.env` as `SLATE_SECRET_KEY`. Never commit or share this file. Keep the key stable;
   changing it makes stored webhook secrets unreadable. Back it up with your data.
3. Choose `SLATE_TIMEZONE` (an IANA name such as `America/New_York`, default `UTC`) and
   optionally `SLATE_PORT` in `.env`. Slate's board and newly created recurrence rules use
   that workspace timezone. Existing recurrence rules keep their own saved zone.
4. Run:

   ```sh
   docker compose config --quiet
   docker compose up --build -d
   curl http://127.0.0.1:8000/healthz
   ```

   Use your chosen port if different. Node runs inside the image's build stage, not on
   your host. An absent or failed web build fails the image build; it is not replaced by
   an empty board. A Git archive needs no `.git`. Its revision is honestly `dev` unless
   `SLATE_REV`/`SLATE_COMMIT` build identity is supplied; normal clones retain Git identity.
   For a Git worktree, set those two values explicitly because its Git directory is outside
   the Docker build context.

5. **Claim the empty server before exposing it anywhere else.** Bootstrap is unauthenticated
   only while no owner exists. Run locally (PowerShell users can use `curl.exe`):

   ```sh
   curl -X POST http://127.0.0.1:8000/api/v1/bootstrap \
     -H 'Content-Type: application/json' \
     -d '{"handle":"alex","display_name":"Alex"}'
   ```

   Save the returned owner token securely; it is shown once. Open
   `http://127.0.0.1:8000` and sign in with that token. Do not give it to an agent. Create
   separate agent accounts and limited tokens through the board's Agents controls or API.
   Repeated bootstrap is refused; this is not a password-reset endpoint.

Compose binds **127.0.0.1 only**, does not enable private webhook targets, and uses its own
`slate-portable` project with a durable `slate-data` named volume mounted at `/data`.
Database, attachments and other server files under `/data` survive container replacement
and `docker compose down`. **`docker compose down --volumes` deletes the data.** Before
upgrades, stop writes and back up the entire volume plus `.env`, or use a SQLite-aware
online backup and separately preserve file assets. Never copy only an actively written
SQLite main file and assume its WAL contents were included.

For remote access, first bootstrap locally, then deliberately configure an authenticated
HTTPS reverse proxy/private network and the appropriate forwarded-header trust. Do not
publish the default unauthenticated bootstrap to a network. For a long-running server
(systemd unit, nightly backups, deploy and rollback scripts) see
[`deploy/server/`](deploy/server/) and its runbook; this root Compose file is the quick
local start.

## Install the Python artifacts

Build from the source root using uv:

```sh
uv build --out-dir dist/server
uv build clients/python --out-dir dist/runner
```

Distribute the resulting wheel files through whatever channel you use. The runner
version follows its protocol and may differ from the server version; install compatible
artifacts from the same source release. Source distributions also retain their required
resources and can be rebuilt away from the checkout.

### Server without Docker (API/MCP only by default)

Create a Python 3.12 virtual environment, install the local server wheel, and activate it.
Example for a POSIX shell, from a new directory outside the source checkout:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python /path/to/slate-0.1.0-py3-none-any.whl
. .venv/bin/activate
export SLATE_DATABASE_PATH="$PWD/data/slate.db"
export SLATE_TIMEZONE="America/New_York"   # any IANA name; the default is UTC
export SLATE_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
# Persist that key securely before restarting; do not generate a new one every startup.
slate migrate
slate serve --host 127.0.0.1 --port 8000
```

Windows PowerShell: the interpreter is `.venv\Scripts\python.exe`, activation is
`.venv\Scripts\Activate.ps1`, and environment variables use `$env:SLATE_DATABASE_PATH`
(and the other names above). Use an absolute path in a directory you own, not a deployment
operator's home directory. `slate migrate` locates installed resources and does not require
an `alembic.ini` or current directory in the repo. Back up before migrating existing data.

The wheel deliberately does **not** contain the compiled board. `/healthz` and `/api/v1`
work; `/` returns 404 without a static build. To serve a board yourself, run `npm ci` and
`npm run build` in the source `web/` directory, copy the complete `web/dist` directory to
your deployment, and set `SLATE_STATIC_DIR` to its absolute path before starting the
server. Docker does this build automatically and is the complete-server distribution.

### Local runner and adapters

Use a separate virtual environment with Python 3.11 or newer and install the local
`slate_runner-<version>-py3-none-any.whl` with `uv pip install --python <venv-python>`.
Activate that environment, then:

```sh
slate-runner --help
slate-runner-assets --output starter
```

The export refuses an existing destination. Read `starter/README.md`, copy
`starter/runner.example.toml` to `starter/runner.toml`, set your local URL, and set
`SLATE_TOKEN` in the runner process environment to an **agent** token scoped to a disposable
list. Run `slate-runner --config starter/runner.toml`, then assign that agent a harmless
test task. The bundled example emits progress and a done result without invoking AI.
Confirm the task actually finishes; a connected stream alone is not executor readiness.
Stop the example before assigning real work. Never start competing resident runners for
one account, and drain active work before upgrades.

Adapters are exported under `starter/hermes/` (Hermes Agent) and `starter/runner-example/`
(a worked example for an agent service reached over HTTP) directly from the canonical
integration sources at build time. Choosing an adapter does not install or log into its
runtime. Explicitly configure your own endpoint, profile/project, owner handle and
credentials (for the example, `RUNNER_API_URL` and `RUNNER_AGENT_ID`). Do not copy a deployment's credentials or use an
owner/admin Slate token. Adapter headers and the integration docs describe prerequisites
and limitations; package import checks are not approval, stop or real-provider acceptance.

Manual REST/MCP use does not need the resident runner. Guided pairing from the board
(**Agents → Add my agent**) is described in [Connect your own agent](docs/connect-your-agent.md).

## Development and verification

```sh
uv sync --frozen
uv run ruff check .
uv run pytest -q
uv run pytest -q clients/python/tests/test_distribution.py
```

Packaging tests build and install wheels in temporary isolated environments, migrate a
fresh database away from the checkout, bootstrap an owner and check authenticated access.
Runner tests also rebuild an extracted sdist and verify adapter bytes against their
canonical sources. They do not contact an AI runtime. Docker/OS acceptance is separate:
a working Python artifact test does not prove a Linux image was built or started.

- Contributor rules: [`AGENTS.md`](AGENTS.md).
- Agent API reference: [`docs/agents/`](docs/agents/).
- Architecture, decisions and the topic index: [`TECHNICAL-GUIDE.md`](TECHNICAL-GUIDE.md); board use: [`USER-GUIDE.md`](USER-GUIDE.md); what changed when: [`docs/RELEASE-NOTES.md`](docs/RELEASE-NOTES.md).

## License

MIT. See `LICENSE`.
