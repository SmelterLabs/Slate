# Slate — agent instructions

Slate is a self-hosted task board where people and AI agents work the same cards. Agents file, take, do and report on the work; the board tells the people who own that work, truthfully and quietly, where each thing stands and what needs them. It is a public product: anyone can install it and connect their own agents. This file is the instruction source for every coding agent working in this repo (Claude Code reads it through `CLAUDE.md`, which is a pointer). Read it fully before the first change.

## Startup reads

1. `.local/AGENTS.md` — before your first reply or action on any task, planning included, check whether this clone has one and read it first. It holds the maintainer's own rules for this clone and names any other local file to read; where it differs from this file on process, it wins. The `.local/` folder is gitignored and never committed; in a Git worktree, look in the main checkout (the folder that holds `git rev-parse --git-common-dir`).
2. `ARCHITECTURE.md` — how Slate works. Read "What Slate is", "Architecture" and "Service layer and the event log", then the `docs/<topic>.md` file for the area you are changing (the index is at the end of `ARCHITECTURE.md`); read the rest when the work reaches it.
3. `git log -15` — what landed last.

## What Slate is for

Judge a change against these four uses. A change that helps one and hurts another is not finished.

- **A person runs their day from the board.** It must just work, stay quiet, and surface what needs them. A card that says it waits on someone when it does not, a question that stays open after it was answered, and a notification nobody needed are defects, not polish.
- **Agents are workers, not integrations.** An agent has an account, takes a card, does the work, talks with people on the card and in chat, and reports. Every agent runtime uses the same public doors (REST, MCP, the runner protocol); no agent is special.
- **A project can include outside people.** A private section holds a project's lists, documents and chat; a guest sees that section and nothing else.
- **Anyone can install it.** One process, one file, one container, safe defaults, and a guided way to add a local agent.

The bar for every change: better for the person using the board, the agent using the API and the developer reading the code, without breaking what already works.

## Product rules (non-negotiable)

1. **Filing an item never creates children.** `POST /items` creates exactly one item. Subtasks come only from an explicit `POST /items/{id}/split` or a `POST /items` with `parent_id`. The board shows what the person filed unless they expand it.
2. **An assignee is an account** (kind `person` or `agent`). Never an agent runtime's profile name, never an email address, never a free-text string.
3. **Every change is an event with an actor.** Every mutation goes through the service layer's single change path, which writes the `events` row (who, source `ui|api|mcp|system`, field, before, after, optional `session_ref`). There is no write path that skips it; a test enforces this.
4. **Agent machinery is history, not columns.** "Running", "blocked", "in review" are events or flags, never statuses Slate adds to a list. Columns are the list owner's words.
5. **Slate knows nothing about Hermes.** Nothing under `src/` may import, reference or know Hermes or any other agent runtime. `integrations/` may hold optional adapters (plain scripts that talk to Slate's public API and to another product's API); deleting `integrations/` must not break any test in `tests/`.
6. **Live or nothing.** Every mutation reaches connected boards through the SSE stream within one second. The UI never polls.
7. **Docs and drawers hold a project's documents; the task is still the unit of work.** A page (edited in place, stored as Markdown, versioned) or a file (uploaded revisions, never replaced) belongs to a section, a list or a drawer. A drawer is a list of documents someone fills on purpose; nothing gathers documents up from across the workspace, and a page is never a wiki for its own sake. Review threads, discussion and a `facts` block are how people talk about and record what a document says. Work still happens on tasks: mentioning or asking an agent on a page is a turn that gets a reply in the thread and starts no task run, and "create task" from a page files one ordinary item.
8. **Simplest thing that works.** One process, one SQLite file, one container. No Postgres, Redis, queues or extra services. If a change seems to need one, stop and raise it in the pull request instead of adding it.
9. **This repository is public. Product only, nothing personal.** A file belongs here only if someone who downloads Slate to install, use, operate or contribute to it needs it. No names of people, family, employers, clients, hosts, machine paths, private agents or private products in any file, fixture or commit message; no journals, work-in-progress lists, private plans or one installation's operating notes. Test data is invented, never copied from a real workspace, document or conversation. Example handles are limited to `alex` (person) and `my-agent`/`other-agent` (agents); example addresses are `example.com`. Documentation describes the product as it is today, never a particular deployment or the story of how the code got here. Deployment-specific values live in the server's `.env`; a maintainer's own notes live in `.local/`. Run `sh scripts/install-hooks.sh` once after cloning: the hooks check each commit against a maintainer's private word list, which is itself never committed (`docs/repository-checks.md`). The check only knows the words on its list, so this rule is what keeps the repository clean.
10. **A card tells the truth about whose move it is.** An agent stops a card on a person only by asking a question (`ask`); a failure, a finished answer or a mention never leaves a card waiting on someone. Every card carries one line saying where it stands. Each set of states is declared once and read through one function (`docs/state-machines.md`). When Slate refuses what an agent tried, the agent says so on the card instead of stopping silently.
11. **One place decides how loud.** What reaches a person, through which channel and how soon is decided in `service/attention.py`, from what happened and how long it has waited. No feature sends its own notification.
12. **A guest sees one project and never a workspace agent.** Every read goes through the one scrub (`access.scrub`) and every write through the scope check. A new table whose rows belong to a task carries `item_id`, or `access.check_object` is taught the one hop to it; a new GET route is covered by `tests/test_guest_boundary.py`.

## Stack (decided; do not swap)

- Python 3.12, FastAPI, SQLite (WAL) through SQLAlchemy 2.x, Alembic migrations, `python-dateutil` for RRULE, `uvicorn`. Dependency management with **uv** (`pyproject.toml` + `uv.lock`).
- The scheduler and the automation, push and email engines run as asyncio tasks inside the same process.
- MCP server (`slate-mcp`) in the same package, Python `mcp` SDK, sharing the service layer with REST. A connection lists a five-tool core unless it asks for more.
- Frontend: Svelte 5 + Vite under `web/`, built in CI into the Docker image and served by FastAPI at `/`. The server is not assumed to have Node; never require a Node build on the host. The board hand-rolls its interactions; adding a UI library is a maintainer decision.
- The built-in AI add talks to an Ollama server named in settings; with none configured the card stays as typed.
- Tests: `pytest` + `httpx` for the API, `hypothesis` for recurrence, Vitest and Playwright for the board.

## Code ownership

This codebase is yours: what it's for and how it's built are your call, and you'll keep building on it.
When code you touch repeats existing code, merge it; when you find a nearby defect, fix it instead of
only reporting it. Three limits: the product rules, gates and out-of-scope lists in this file still win;
a worker seat owns only the files its task file names, so outside those it reports instead of editing;
and any change to existing user-facing behaviour that nobody asked for is called out explicitly in the
final report and PR, which list every changed file, not just the large changes.

## How to write code here

- Fewer, deeper modules with smaller interfaces.
- Check data once, where it comes in, then just use it.
- Write one-off logic where it is used; no helper with one caller, no option nobody asked for.
- No `try`/`except` unless the failure is real and handled there (outside I/O, parsing someone else's file).

## How to work here

- **A question, a review or an exploration gets an answer or a plan.** Do not edit files or open a branch until asked to build.
- **One change, one branch, one pull request.** Start from the latest `main`; squash-merge. Never commit directly to `main`.
- **TDD per task:** write the failing test, run it, implement, run green, commit. Small commits.
- **Run before claiming done:** `uv run ruff check . && uv run pytest -q`, and for a change under `web/` the board checks in Commands. CI runs the same plus the browser tests.
- **Do not break what works.** A change to a state, a run ending, a closing line or a wire shape is checked against everything that speaks it: the runner and clients in `clients/`, the adapters in `integrations/`, and the board. They ship together or the change is not done.
- **Regenerate the agent reference** with `uv run slate docs` whenever you add or change a route, an MCP action, a docstring or an event type (add its line to `docgen.EVENT_DOCS`); `tests/test_agent_docs.py` fails until you do. The agent-facing docs are `docs/agents/`.
- **Update `ARCHITECTURE.md`** in the same pull request when the structure changes (a new module, a schema or write rule, a gotcha). It is an index plus the architecture; feature internals live in the topic files under `docs/` it points to, so a change to a feature updates its topic file. These files say how the product works, in enough detail that an agent can work from them alone. They hold no decision history: why a change was made and what was rejected go in the pull request. `tests/test_architecture_doc.py` fails when a source module is missing from the Modules table, the table names a file that is gone, a cited doc does not exist, or a topic doc is not in the index; what the prose says is yours to keep true. Update `USER-GUIDE.md` when user-facing behaviour changes. Keep both proportionate, and write them in the present tense: what the product does, not what changed.
- **Say why in the pull request.** Each pull request and commit message says what changed and why. Questions go in the pull request, not into silent choices.
- **Report in plain words.** Say what changed, what was verified and what is still open; "done" and a list of open items do not go in the same report. A decision put to the maintainer is a real question with its options, or it is not raised.
- **What belongs in this file:** rules that hold for every installation. One installation's process goes in `.local/AGENTS.md`; notes about one agent runtime go with that runtime's adapter.
- **Out of scope** (do not build without a maintainer decision): two-way sync with another task tool, calendar sync, a public hostname by default, anything specific to one agent runtime in `src/` (adapters under `integrations/` are allowed, per rule 5), and these removed features: custom fields, dashboards, goals, sprints, time tracking, estimates, workload and capacity, forms, Slack/Discord channels, email-to-task, a GitHub integration, task and status templates, duplicate merge, a ClickUp importer, a Workload page and an Import page on the board, numbered priority tiers, the Timeline, Box and Calendar layouts, and the board's automations builder page (the automations engine and its REST and MCP API stay). The Table layout is kept but frozen.
- **Secrets:** none in the repo, ever. Tokens are shown once at creation and stored hashed; an automation's signing secret is encrypted under `SLATE_SECRET_KEY`, because Slate has to sign with it. `.env` is gitignored.
- **Time:** all stored timestamps are UTC ISO 8601; display and recurrence use the workspace timezone (`SLATE_TIMEZONE`, an IANA name, default `UTC`).

## Commands

```
uv sync                                  # install
uv run uvicorn slate.app:app --reload    # dev server on :8000
uv run pytest -q                         # tests
uv run ruff check . && uv run ruff format .
uv run slate docs                        # regenerate docs/agents/reference
uv run alembic revision --autogenerate -m "..."   # new migration
uv run alembic upgrade head
cd web && npm ci && npm run dev          # board dev server (proxies /api to :8000)
cd web && npm run check && npm run test:unit && npm run build   # board checks
```
