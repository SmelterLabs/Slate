# Slate — agent instructions

Slate is a self-hosted task board, built to be used primarily by AI agents and looked at by the people who own the work. This file is the instruction source for every coding agent working in this repo (Claude Code reads it through `CLAUDE.md`, which is a pointer). Read it fully before the first change.

## Startup reads

1. `TECHNICAL-GUIDE.md` — architecture, how to run and test, the Decisions log, and the index of `docs/<topic>.md` files that hold each feature's internals.
2. `git log -15` — what landed last.
3. `.local/AGENTS.md`, if this clone has one — a maintainer's own notes for this clone. The `.local/` folder is gitignored and never committed; in a Git worktree, look in the main checkout (the folder that holds `git rev-parse --git-common-dir`).

## Product rules (non-negotiable)

1. **Filing an item never creates children.** `POST /items` creates exactly one item. Subtasks come only from an explicit `POST /items/{id}/split` or a `POST /items` with `parent_id`. The board shows what the person filed unless they expand it.
2. **An assignee is an account** (kind `person` or `agent`). Never an agent runtime's profile name, never an email address, never a free-text string.
3. **Every change is an event with an actor.** Every mutation goes through the service layer's single change path, which writes the `events` row (who, source `ui|api|mcp|system`, field, before, after, optional `session_ref`). There is no write path that skips it; a test enforces this.
4. **Agent machinery is history, not columns.** "Running", "blocked", "in review" are events or flags, never statuses Slate adds to a list. Columns are the list owner's words.
5. **Slate knows nothing about Hermes.** Nothing under `src/` may import, reference or know Hermes or any other agent runtime. `integrations/` may hold optional adapters (plain scripts that talk to Slate's public API and to another product's API); deleting `integrations/` must not break any test in `tests/`.
6. **Live or nothing.** Every mutation reaches connected boards through the SSE stream within one second. The UI never polls.
7. **Docs and drawers hold a project's documents; the task is still the unit of work.** A page (edited in place, stored as Markdown, versioned) or a file (uploaded revisions, never replaced) belongs to a section, a list or a drawer. A drawer is a list of documents someone fills on purpose; nothing gathers documents up from across the workspace, and a page is never a wiki for its own sake. Review threads, discussion and a `facts` block are how people talk about and record what a document says. Work still happens on tasks: a doc Ask reaches an inbox and a check-in but starts no runner, and "create task" from a page files one ordinary item.
8. **Simplest thing that works.** One process, one SQLite file, one container. No Postgres, Redis, queues or extra services. If a change seems to need one, stop and raise it in the pull request instead of adding it.
9. **This repository is public. Product only, nothing personal.** A file belongs here only if someone who downloads Slate to install, use, operate or contribute to it needs it. No names of people, family, employers, clients, hosts, machine paths, private agents or private products in any file, fixture or commit message; no journals, work-in-progress lists, private plans or one installation's operating notes. Test data is invented, never copied from a real workspace, document or conversation. Example handles are limited to `alex` (person) and `my-agent`/`other-agent` (agents); example addresses are `example.com`. Documentation describes the product as it is today, never a particular deployment or the story of how the code got here. Deployment-specific values live in the server's `.env`; a maintainer's own notes live in `.local/`. `scripts/check_private_terms.py` checks every tracked file, the staged bytes and the commit message against a maintainer's denylist of hashed words. **The denylist itself is never committed** (a hashed name or hostname is recovered by guessing, so a committed list publishes its words): it lives in the clone's Git directory (`.git/private_terms.sha256`) and, for CI, in the `PRIVATE_TERMS_SHA256` repository secret; a clone without a list checks nothing. Run `sh scripts/install-hooks.sh` once after cloning so the pre-commit and commit-msg hooks run the check before CI. Add a word with `python scripts/check_private_terms.py --add WORD`, then update the secret from the same file. The check only knows the words on the list: it cannot see a real name it was never told, so the rule above is what keeps the repository clean.

## Stack (decided; do not swap)

- Python 3.12, FastAPI, SQLite (WAL) through SQLAlchemy 2.x, Alembic migrations, `python-dateutil` for RRULE, `uvicorn`. Dependency management with **uv** (`pyproject.toml` + `uv.lock`).
- Scheduler (recurrence, due events, webhook delivery) runs as asyncio tasks inside the same process.
- MCP server (`slate-mcp`) in the same package, Python `mcp` SDK, sharing the service layer with REST.
- Frontend: Svelte 5 + Vite under `web/`, built in CI into the Docker image and served by FastAPI at `/`. The server is not assumed to have Node; never require a Node build on the host.
- Tests: `pytest` + `httpx` for the API, `hypothesis` for recurrence, Playwright for the board.

## Code ownership

This codebase is yours: what it's for and how it's built are your call, and you'll keep building on it.
When code you touch repeats existing code, merge it; when you find a nearby defect, fix it instead of
only reporting it. Three limits: the product rules, gates and out-of-scope lists in this file still win;
a worker seat owns only the files its task file names, so outside those it reports instead of editing;
and any change to existing user-facing behaviour that nobody asked for is called out explicitly in the
final report and PR, which list every changed file, not just the large changes.

## How to work here

- **One change, one branch, one pull request.** Start from the latest `main`; squash-merge. Never commit directly to `main`.
- **TDD per task:** write the failing test, run it, implement, run green, commit. Small commits.
- **Run before claiming done:** `uv run ruff check . && uv run pytest -q`. CI runs the same.
- **Regenerate the agent reference** with `uv run slate docs` whenever you add or change a route, an MCP action, a docstring or an event type (add its line to `docgen.EVENT_DOCS`); `tests/test_agent_docs.py` fails until you do. The agent-facing docs are `docs/agents/`.
- **Update `TECHNICAL-GUIDE.md`** in the same pull request for anything a new contributor would need to know (new module, decision, gotcha). It is an index plus the architecture and the Decisions log; feature internals live in the topic files under `docs/` it points to, so a change to a feature updates its topic file. Update `USER-GUIDE.md` when user-facing behaviour changes. Keep both proportionate, and write them in the present tense: what the product does, not what changed.
- **Say why in the pull request.** Each pull request and commit message says what changed and why. Questions go in the pull request, not into silent choices.
- **Out of scope** (do not build without a maintainer decision): two-way sync with another task tool, calendar sync, a public hostname by default, anything specific to one agent runtime in `src/` (adapters under `integrations/` are allowed, per rule 5), and these removed features: custom fields, dashboards, goals, sprints, time tracking, forms, Slack/Discord channels, email-to-task, a GitHub integration, task and status templates, duplicate merge, a ClickUp importer, a Workload page and an Import page on the board, the Timeline, Box and Calendar layouts, and the board's automations builder page (the automations engine and its REST and MCP API stay). The Table layout is kept but frozen.
- **Secrets:** none in the repo, ever. Tokens are shown once at creation and stored hashed. `.env` is gitignored.
- **Time:** all stored timestamps are UTC ISO 8601; display and recurrence use the workspace timezone (`SLATE_TIMEZONE`, an IANA name, default `UTC`).

## Commands

```
uv sync                                  # install
uv run uvicorn slate.app:app --reload    # dev server on :8000
uv run pytest -q                         # tests
uv run ruff check . && uv run ruff format .
uv run alembic revision --autogenerate -m "..."   # new migration
uv run alembic upgrade head
cd web && npm ci && npm run dev          # board dev server (proxies /api to :8000)
```

## Repo layout

```
src/slate/            package: app.py (FastAPI), db.py, models.py, service/ (the only place that writes), api/ (routers), mcp_tools.py + mcp_server.py, scheduler.py
alembic/              migrations
tests/
web/                  Svelte board
clients/              Python and TypeScript clients, the resident runner
integrations/         optional adapters and the agent skill
deploy/server/        compose, systemd units, backup timer, runbook
docs/<topic>.md       one file per feature area (indexed in TECHNICAL-GUIDE.md); docs/agents/ is the agent-facing kit
```
