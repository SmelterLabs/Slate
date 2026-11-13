# Slate — Technical Guide

A briefing for an agent picking this up with zero context: what Slate is, how it is built, the
rules every write obeys, how to run and deploy it, the design decisions with their reasons, and the
gotchas. Feature internals live in the topic files indexed at the end; keep this file an index
plus the parts below, and update the topic file in the same PR as the change it describes.

## What Slate is

A single-process task service (FastAPI + SQLite) with three doors onto the same service layer: a
REST API at `/api/v1`, an MCP server at `/mcp` (and stdio), and a Svelte board at `/` fed live by
Server-Sent Events. Primary users are agents; the board is for the people who own the work.

## Architecture

```
            ┌──────────── one process (uvicorn) ────────────┐
 board ──SSE┤  api/ routers ─┐                               │
 agents REST┤                ├─► service/ (apply_change) ─► SQLite (WAL)
 agents MCP ┤  mcp_server ───┘        │                      │
            │  scheduler (asyncio): recurrence, due events,  │
            │  webhook delivery ◄── events table             │
            └───────────────────────────────────────────────┘
```

- `service/` is the only module allowed to write to the database. Every write goes through
  `apply_change()`, which records the `events` row and publishes to the in-process event bus (SSE +
  webhook queue).
- Routers and the MCP server are thin: validate input, call a service function, shape output.
- Background asyncio tasks start in `lifespan` and are registered with `monitor.track`: the
  scheduler (nine hooks on one tick: recurrences, due events, reminders, digest, trash purge, lease
  expiry, chat-turn expiry, doc-turn expiry, session-expiry notices), the webhook dispatcher, the
  automations engine and the push engine; a thread warms the item cache. Every one must be
  idempotent on restart.

### Modules

`ls src/slate src/slate/service src/slate/api` is the list of record; this table says what each
one is for. Routers in `api/` are thin (validate, call a service function, shape output) and are
mounted in the order of `api.MODULES`; `api/events.py` and `api/recurrences.py` are mounted by
`app.py` through their own `install(app)`.

| Path | What it is |
|---|---|
| `app.py` | `create_app()`: routers, `BoardFiles` (the built board, security headers), `/healthz`, `GET /health`, the lifespan (scheduler, dispatcher, automations, push, cache warm-up, the MCP session manager). |
| `settings.py` | `Settings` (pydantic-settings, `SLATE_*` env vars; every one is in `env.example`). |
| `db.py` | Engine, `SessionLocal`, `get_session`, the write-guard hooks on every `Session`, `SerialCursor` (`docs/fast-lane.md`). |
| `models.py` | Every table, SQLAlchemy 2 `Mapped` style, `UTCDateTime`, enums, the SQLite triggers (`TRIGGERS`). |
| `ids.py` | `new_id()` 12-char base62 keys; `new_event_id()` time-ordered ids for events and messages. |
| `dates.py` | `parse_when()` (the date rule), `local_day_bounds()`, `run_at()`, `is_date_only_input()`. |
| `crypto.py` | AES-GCM for webhook and automation secrets under `SLATE_SECRET_KEY`. |
| `rrule_text.py` | One RRULE in plain English (`describe`). |
| `scheduler.py` | The asyncio tick loop and `hooks`/`POLLERS` (`docs/recurrence.md`). |
| `notify.py` | Webhook dispatcher (HTTP side, `attempt`, `post_capped`, `Dispatcher`) and the due/reminder/digest scheduler hooks. |
| `automator.py`, `pusher.py`, `emailer.py` | Bus-woken engine loops for automations, Web Push and email notifications (`mailer.py` is the plain SMTP client; `service/email.py` decides who is emailed). |
| `webpush.py` | Web Push protocol: VAPID, ES256 JWT, aes128gcm. |
| `fastlane.py` | `Timing`, GZip, `Conditional` (ETags from the event cursor, `CACHEABLE`). |
| `batch.py` | `/batch` and `slate_batch`: many MCP actions in one request, atomic by default. |
| `ratelimit.py`, `presence.py`, `monitor.py` | In-memory per-token buckets, who is around, what the Health page counts. |
| `mcp_tools.py`, `mcp_server.py` | The MCP tool table and the server (`docs/mcp-server.md`). |
| `docgen.py` | Generates `docs/agents/reference/` (`uv run slate docs`); `EVENT_DOCS`. |
| `build.py` | The build revision (`rev`, `commit`) on `/healthz`. |
| `cli.py` | `slate serve|migrate|import clickup|docs|check-targets|backfill-doc-text`. |
| `connection_models.py`, `runner_compat.py` | Agent connection rows; the runner protocol floor (426 for old runners). |
| `importers/` | The one-time ClickUp importer (`docs/importer.md`). |
| `service/core.py` | `apply_change()`, `Actor`, the write guard, `BOOKKEEPING`, `event_payload()`. |
| `service/bus.py` | In-process pub/sub the stream, dispatcher and engines wake on. |
| `service/errors.py` | `NotFound`/`Conflict`/`Invalid`/`Forbidden`/`Gone`/`Unavailable` → 404/409/422/403/410/503. |
| `service/accounts.py`, `sessions.py`, `collaboration.py`, `connections.py` | Accounts and tokens; guest-session expiry notices; invitations and cookie sign-in; agent pairing. |
| `service/access.py`, `permissions.py`, `audit.py` | Roles, shares, token limits and the two enforcing hooks; the writes behind them; the audit log (`docs/permissions.md`). |
| `service/aiadd.py` | AI add: `POST /items/ai` files one item from plain words, then the configured Ollama model (`SLATE_AI_URL`) fills it in as `slate` after the response. |
| `service/sections.py`, `lists.py`, `tags.py`, `views.py`, `favorites.py` | Structure, statuses (`move_item_to_status` decides `completed_at`), tags, saved views, favorites. |
| `service/items.py`, `item_cache.py`, `links.py`, `trash.py`, `export.py`, `search.py`, `revert.py` | Items and their views; the cached view JSON; dependencies and the derived blocked state; trash and purge; JSON/CSV export; one search; revert from the log. |
| `service/comments.py`, `checklists.py`, `attachments.py`, `watchers.py`, `activity.py` | The task window's parts (`docs/task-window.md`). |
| `service/item_reads.py` | Comment dots: when each account last opened each item (`docs/board.md`). |
| `service/today.py`, `inbox.py`, `checkin.py`, `brief.py` | Today; the inbox derived from events; the agent check-in; the paste-ready brief. |
| `service/reports.py`, `work.py`, `agents.py`, `chatwake.py` | Agent badges, the work loop and leases, the roster and plan, chat-mention wakes (`docs/agents-work-loop.md`). |
| `service/recurrences.py`, `due.py`, `reminders.py` | Rules and the materializer; due events and the run stage; reminders and the digest. |
| `service/webhooks.py`, `deliveries.py` | Subscriptions; the durable delivery queue and its prune. |
| `service/automations.py`, `push.py` | The automation engine step and vocabulary; push subscriptions and the push engine step. |
| `service/chat.py` | Channels, messages, threads, reactions, exact read exposure (`docs/chat.md`). |
| `service/chat_files.py` | Files on chat messages: upload to a channel, send with a message, read where the message is read (`docs/chat.md`, "Files on messages"). |
| `service/docs.py`, `doc_threads.py`, `doc_compare.py`, `doc_hub.py` | Pages, file docs and drawers; discussion and review threads; extract and compare; the project hub (`docs/docs-and-files.md`). |
| `service/workload.py`, `health.py` | Capacities and the Box view's numbers; the health report. |
| `api/auth.py`, `api/schemas.py`, `api/uploads.py`, `api/longpoll.py` | `require(scope)` and the `Auth` aliases; the core request/response models; counted raw upload bodies; the long-poll helpers. |
| `api/*.py` (the rest) | One router per resource: accounts, connections, collaboration, permissions, sections, lists, items, comments, checklists, attachments, tags, today, views, favorites, inbox, chat, webhooks, links, stats, batch, agents, checkin, work, automations, reminders, docs, doc_comments, workload, data (export, backup, empty trash), health, search; plus `events` and `recurrences`. |

## Schema notes

- **Ids** are 12-char base62 (`0-9A-Za-z`) strings made in Python. No `_`, so a token
  `slate_<id>_<secret>` splits cleanly. Event ids are time-ordered (7 chars of milliseconds + 5
  random; a per-process logical clock means no ties), so `ORDER BY id` is commit order and the
  event stream uses the id as its `since` cursor.
- **Timestamps** use `UTCDateTime`: aware UTC datetimes stored as fixed-width ISO 8601 text
  (`2026-10-23T17:00:00.000000Z`). Naive datetimes raise. Text order is time order.
- **Enums** are `VARCHAR` + `CHECK` (non-native), so a bad value fails in the database, not only in
  Python.
- **Rules held by the database:** tag names unique and lowercase (`CHECK name = lower(name)`); item
  depth ≤ 4 (a top task plus four levels of subtasks), no cycles, and no
  reparent that would push a subtree past four (triggers `items_depth_*`); events append-only
  (triggers `events_no_*`); `(recurrence_id, occurrence_at)` unique, for idempotent recurrence
  spawning.
- **Triggers are not autogenerated by Alembic.** They live in `models.TRIGGERS` (attached to
  `create_all` for tests) and are repeated verbatim in the initial migration;
  `tests/test_migrations.py` fails if the two drift. A future migration that changes a trigger must
  drop and recreate it by hand.
- **`events.item_id` and `events.subject_id` have no foreign key** on purpose: history outlives its
  subject (an undo hard-delete keeps its `item.deleted` event), and a cascading FK would need to
  rewrite an append-only table.
- **`events.subject_id`**: the id of the row the event is about (account, token,
  list, section…; equals `item_id` for item events). The type prefix names the table.
- **`items.last_due_event`**: the last due stage emitted for the item, which makes due events
  idempotent (`docs/webhooks.md`, Due events).
- **`lists.default_status_id` ↔ `statuses.list_id`** is a circular FK; `use_alter=True` names it,
  SQLite checks FKs on write so creation order does not matter.
- **New migration:** `uv run alembic revision --autogenerate -m "..."`, then replace any
  `slate.models.UTCDateTime(length=27)` in the generated file with `sa.String(length=27)`
  (migrations must not import app code), and run `uv run pytest tests/test_migrations.py` (it runs
  `upgrade`, `alembic check` and `downgrade` on a temp file).

## Service layer and the event log

Rule 3 (every change is an event with an actor) is enforced in code, not by convention.

**`Actor(account_id, source, session_ref=None)`** says who and through which door
(`ui|api|mcp|system`). The API builds it per request in `require()`; MCP and the scheduler
(`source=system`) build their own.

**`apply_change(session, actor)`** is a context manager yielding a `Change`:

```python
with apply_change(session, actor) as cx:
    # insert + event (after = snapshot)
    item = cx.create(Item(...), "item.created")
    # one field + event (before -> after); returns False and records nothing if unchanged
    cx.set(item, "title", "New", "item.updated")
    # delete + event (before = snapshot)
    cx.delete(obj, "item.deleted")
    # any other write: do it yourself, name the rows the event accounts for
    cx.record("item.tagged", item_id=item.id, after=[...], covers=[item_tag_row])
```

On leaving the outermost block it flushes, commits, then publishes each event (`event_payload()`
shape) to `service.bus.bus`. Any exception rolls back and publishes nothing. Inner `apply_change()`
blocks join the outer one (same actor required), so service functions can call each other and still
commit once. Event payloads never include `token_hash`, `secret_hash` or `secret_enc`
(`core.SECRET_COLUMNS`).

**The guard** (`core.guard_flush`/`guard_bulk`, hooked onto every `Session` in `db.py` so import
order cannot skip it):

- any insert/update/delete flushed with no `apply_change()` open → `WriteOutsideChange`;
- inside a block, any written object not passed through `create`/`set`/`delete`/`record(covers=...)`
  → `WriteWithoutEvent`;
- ORM bulk `insert()/update()/delete()` outside a block → `WriteOutsideChange`; inside a block it
  must record at least one event;
- an `Event` modified or deleted → refused (and the database triggers refuse it too).

The exemptions: `core.BOOKKEEPING` (`Token.last_used_at`, refreshed by `resolve_token()` at most
once a minute) and `core.BOOKKEEPING_MODELS` (`Delivery` rows: the deliveries table is itself the
webhook delivery log; logging each attempt as an event would flood the log and loop back into
webhooks). Raw `engine.connect()` SQL is not guarded; only tests use it.

**How to add a mutation:** write a function in `slate/service/<area>.py` taking `(session, actor,
...)`, validate, then do every write through `cx.*` inside `apply_change()`. Then classify it in
`tests/test_no_write_without_event.py` (`MUTATIONS` with a scenario, or `READS`): that test walks
every public function in `slate.service` and fails on anything unclassified, and runs each mutation
scenario asserting it left an event with the caller as actor.

**Event types.** The full list, one line each, is generated into `docs/agents/reference/events.md`
from `docgen.EVENT_DOCS` (`uv run slate docs`); `tests/test_agent_docs.py` fails when a type in code
has no line or a line has no code. Naming: `<table singular>.<verb past tense>`. `item.updated`
carries `field` (`title`, `due_at`, `body_md`, `tags`, `completed_at`...); `item.assigned` and tag
changes carry the whole before/after handle or name list; events about a non-item row put its id in
`subject_id`. The board hears only the types listed in `store.svelte.ts` `EVENT_TYPES`
(`tests/test_board_event_types.py` keeps that list complete).

## Core REST and the item rules

All under `/api/v1`. Reads need `read`, writes `write`. Service errors map to
HTTP in `api/__init__.py`: `NotFound` 404, `Conflict` 409 (duplicate slug or tag), `Invalid` 422
with a plain-English `detail` (rule violations: depth, stranded statuses, bad dates, undo window),
`Forbidden` 403 (editing someone else's comment). Pydantic shape errors are FastAPI's usual 422
list.

**Items, the rules in code (`service/items.py`):**

- `create_item()` inserts exactly one row. `parent_id` may nest four deep (`items.MAX_DEPTH`; 422
  past that, and the DB triggers back this up; see `docs/task-window.md`, Subtasks); a subtask
  always lives in its parent's list: `bulk_update` with `list_id` moves a task's subtasks with it
  (status mapped by kind, `_status_in`) and refuses a subtask whose parent is not in the same call.
  `split()` is the only multi-child path and records `item.split` on the parent listing the child
  ids.
- Every body change (and a non-empty body at create) writes a `body_versions` row, numbered per
  item. At create the first version rides on `item.created` (`cx.create(..., covers=[version])`).
- `status` is an id or a name (case-insensitive) within the item's list.
  `lists.move_item_to_status()` stamps `completed_at` on entering a `done`-kind status and clears it
  on leaving; `set_statuses()` uses the same function for items it moves, and re-stamps items on a
  kept status whose kind crosses done.
- `assignees` (handles or ids) and `tags` (names; unknown tags are created, each a `tag.created`)
  are whole-set replacements, one event naming before/after.
- Archive is soft and cascades to subtasks (both ways). Hard `DELETE /items/{id}` is an undo:
  creator only, within 10 minutes, refused if it has subtasks; DB cascades remove its
  comments/tags/assignees/versions; its events stay plus `item.deleted`.
- `PATCH` uses `exclude_unset`: send only what changes, `null` clears.

**The date rule (`slate/dates.py`):** `due_at`, `start_at`, `due_before`, `due_after` accept ISO
8601 or date-only `YYYY-MM-DD`. Date-only means 17:00 that day in the workspace timezone
(`SLATE_TIMEZONE`, an IANA name, default `UTC`; with `America/Chicago`, say, that is 22:00Z in
summer and 23:00Z in winter). A naive ISO datetime is read as workspace time, not UTC. Stored as
aware UTC.
**`items.due_date_only`** records that a due date was given without a time (a bare `YYYY-MM-DD`;
`dates.is_date_only_input`). `service/items.py` sets it with every `due_at` write (`_due_date_only`;
internal callers such as recurrences and the ClickUp importer pass `due_date_only` themselves), and
a change of the flag alone is its own `item.updated` event (field `due_date_only`). The board hides
the time and sends bare dates for date-only due dates; an explicit 17:00 stays a time.
`start_at` has no flag (17:00 reads as "no time" there).

**Querying items (`query_items()`, `GET /items`):** every filter ANDs.

| Filter | Meaning |
|---|---|
| `list_id`, `section_id` | exact |
| `status` | status id, or name case-insensitive across all lists |
| `assignee` | account handle or id |
| `tag` | tag name (any case) or id |
| `external_ref` | exact; the filer's own reference (archived/trashed rules apply as for every filter) |
| `due_before`, `due_after` | exclusive bounds; the date rule applies |
| `q` | case-insensitive substring of title or body |
| `kind` | `task` or `doc` |
| `include_archived` | default false hides archived items **and** items in archived lists/sections |
| `stale` | not done/cancelled, and either (a) >=3 `item.updated` events on `due_at` whose previous value was set (first-time setting is not a push), or (b) in an `open`-kind status (or none) with no event of any kind for >=30 days (comments count as touches) |

Unknown handles, tags or status names match nothing (empty list), not an error. Order: `position`,
then `created_at`. Pagination is `limit`/`offset` (`docs/fast-lane.md`). New items get `position =
max + 1` in their list. `views()` turns rows into output dicts with a fixed set of batch queries
(statuses, assignees, tags, subtask counts, links…), never one per item. Measured: 10,000 items in
5 lists, `GET /items?list_id=&tag=` returns 176 rows in ~11 ms median through the test client
(`tests/test_perf.py`, marker `perf`; skip with `-m "not perf"`).

**`external_ref` is unique when set** (partial unique index `ux_items_external_ref`, migration
`3c8a1e5f7b20`): one card per thing an outside filer tracks. `service/items.py` `_claim_ref` checks
inside the change scope (the write lock makes check-then-write race free; the index is the backstop)
on create, split, PATCH and bulk update, and raises `errors.ExternalRefTaken` (a `Conflict` carrying
`existing_id`). The API's 409 body is `{"detail": "external_ref already used by <id>[, which is
closed: reopen that card instead of filing a new one]", "existing_id": "<id>", "existing_state":
"open|closed|archived|trashed"}`; MCP returns the same text as the error. Closed, archived and
trashed items keep their reference (a recurring incident reopens its card, it does not
get a new one), and the detail tells the filer to reopen, unarchive or restore it. A blank reference
is stored as null. Recurrences never copy `external_ref`.

**Today (`GET /today`, `service/today.py`):** the caller's open items (not done/cancelled, not
archived) in three groups, each item in the first that fits: `overdue` (due before now), `today`
(due before midnight workspace time), `waiting` (`waiting_on_account_id` is the caller). Each
sorted by priority (urgent, high, normal, low, none), then soonest due. Also returns `now` and
`day_ends_at`.

**Statuses (`PUT /lists/{id}/statuses`):** replaces the set in order, one `list.statuses_changed`
event. A spec keeps an existing status by `id`, else by name; unmatched existing statuses are
removed. If items sit on a removed status, `fallback` (name or id in the new set) is required, else
422 and nothing changes. Renaming two statuses into each other's names in one call hits the
`(list_id, name)` unique constraint; do it in two calls. A new list gets `lists.DEFAULT_STATUSES`
(To do, In progress, Done) unless it is created with its own.

**How to add an endpoint:** (1) write the service function in `service/<area>.py` (`(session, actor,
...)`, validate first, write only through `cx.*` inside `apply_change()`); (2) classify it in
`tests/test_no_write_without_event.py` (`READS`, or `MUTATIONS` with a scenario); (3) add
request/response models to `api/schemas.py`; (4) add the route to the resource's router with
`ReadAuth`/`WriteAuth`, passing `auth.actor`; (5) a new router module goes into the tuple in
`api/__init__.py`; (6) add the route to `tests/test_openapi.py` and an API test.

## Auth

- Token plaintext `slate_<token id>_<secret>` (secret = 32 random bytes, urlsafe). Stored:
  `sha256(plaintext)` hex only. The plaintext appears in exactly two responses: `POST /bootstrap`
  and `POST /accounts/{id}/tokens`.
- `resolve_token()` parses the id, loads that row, constant-time compares the hash, and rejects
  revoked tokens and disabled accounts.
- **Scopes are ranked** `read < write < admin`; a token's effective level is its highest scope.
  `require("read")` accepts write and admin tokens.
- `require(scope)` → 401 (with `WWW-Authenticate: Bearer`) for a missing/invalid/revoked token; 403
  for too low a scope. Returns `Auth(principal, actor)`; `auth.actor` goes to service writes.
- **`session_ref`**: optional `X-Slate-Session-Ref` header (≤200 chars) on any request; lands in
  every event that request writes.
- **Bootstrap:** `POST /api/v1/bootstrap {handle, display_name}` with no auth, only while the
  accounts table is empty: creates a `person` with an `admin` token (the new account is the actor of
  its own events). After that: 410.
- `DELETE /tokens/{id}`: admins revoke any token; any account revokes its own (needs only `read`).
  Revoking twice is a no-op 204.
- **The owner outranks admins.** Only the owner (or the account itself) mints or revokes tokens on
  the owner account, or disables/enables it (`accounts._require_owner_for_owner`: an admin who
  could mint an owner token would be the owner). Renaming stays admin.
- Handles: `^[a-z][a-z0-9_-]{1,31}$`.

## Run / test / build

See `AGENTS.md` → Commands. Python is managed by uv (`uv python install 3.12`); `uv sync` creates
`.venv`.

## Deploy

The step-by-step procedure for a long-running server is `deploy/server/RUNBOOK.md` (service user,
`.env`, first deploy, https, bootstrap accounts, verify, update, rollback, logs, restore drill). The
examples use the service user `slate`, install dir `/opt/slate` (repo `/opt/slate/repo`, data
`/opt/slate/data`, env `/opt/slate/.env`) and `https://slate.example.com`; put your own values in
the server's `.env`, not in the repo.

- **Build on the host.** The server clones the repository (a private fork needs only a read-only
  deploy key) into `/opt/slate/repo` and `docker compose -p slate up -d --build` builds the image
  there; Node exists only inside the Dockerfile's build stage. CI publishes no image: nothing
  pulls one, and a publish job would need a token that can write packages.
- **Files:** `deploy/server/compose.yml` (project `slate`, container `slate`, `127.0.0.1:18448:8000`,
  `/opt/slate/data:/data`, `env_file` `/opt/slate/.env`, `init`, 20 s stop grace, json-file logs
  10m×5); `slate.service` (user unit, oneshot + RemainAfterExit, reload = rebuild);
  `slate-backup.service/.timer` + `backup.sh` (daily at 03:30, SQLite online backup API inside the
  container, integrity-checked, 30-day prune in `/opt/slate/data/backups/`); `deploy.sh [ref]`
  (idempotent install/update for any `SLATE_HOME`, waits for health, adds Tailscale Serve when
  `SLATE_TAILSCALE_PORT` is set); `rollback.sh <ref>`; `env.example` (every
  setting).
- **Exposure:** the container publishes on loopback only; put a TLS reverse proxy (Tailscale Serve,
  Caddy, nginx) in front of `127.0.0.1:18448`, on a private network unless you decide otherwise.
- **`/healthz`** (unauthenticated) adds `scheduler_last_tick_age_s` (null when the scheduler is
  disabled or has not ticked) and `webhooks_enabled` (`SLATE_SECRET_KEY` set) for deploy checks;
  `rev`, `commit` and `board` carry the build (`docs/design-system.md`, Build revision). Queue
  counts are on the authenticated health report, not here; `db_path` is on the authenticated `GET
  /api/v1/stats`.
- **Gotchas:** every `docker compose` call passes `-p slate` (a test enforces it in
  `deploy/server/`). The container `CMD` `exec`s uvicorn so SIGTERM reaches it rather than the shell.
  `deploy.sh`/`rollback.sh` wrap their body in `main()` so `git checkout` rewriting the running
  script is harmless. systemd eats `$(`; unit files write `$$(`. Rolling back past a migration needs
  a backup restore (the schema does not downgrade). `tests/test_deploy.py` lints all of this locally
  (`bash -n`, unit keys, `docker compose config` when Docker is up).

## Decisions log

Design choices and the reason for each, with the alternatives that were rejected, grouped by area.

### Foundations

- **Build rather than adopt.** Existing trackers (Vikunja, Plane, Huly) were evaluated and the
  project chose to build its own: one host, one container, one SQLite file.
- **uv** for Python dependency management (fast, lockfile, manages the interpreter; easy to install
  locally and in CI). Each runtime floor in `pyproject.toml` is the version `uv.lock` pins, capped
  below the next major, so a wheel install never gets a version the tests did not run; moving the
  lock moves the floor. `tests/test_portable_package.py` installs the wheel with the lock's
  versions as constraints and checks them.
- **Svelte 5** for the board (small bundle, simple reactivity for a live board).
- **The board hand-rolls its interactions:** HTML5 drag and drop and a small escape-first markdown
  renderer, not a DnD or markdown library (the only runtime packages are `exceljs` and `mammoth`,
  which read Office files for previews in `lib/office.ts`); the whole item set is held in memory
  rather than fetched per list; Vitest for unit tests (shares the Vite config). Rejected: SvelteKit
  (no routing or SSR need), a UI kit.
- **Rule 3 is enforced by a guard plus a walk test**, not by review
  (`before_flush`/`do_orm_execute`). Rejected: a decorator-only convention (silently skippable)
  and database triggers writing events (no actor available in SQL).
- **Time-ordered event ids** instead of an autoincrement column, to keep one id style across tables
  while giving the stream a cursor.
- **An event's `source` names the door it came through.** The board sends `X-Slate-Source: ui`
  with `X-Slate-Client: browser`, so History and the audit log say "via the board"; any other REST
  call is `api`, and the header cannot claim `mcp` or `system`. Writes through the stdio bridge
  `slate-mcp` are `api` too (the bridge is a REST client); only HTTP `/mcp` calls record `mcp`.
- **Item API conventions.** A naive ISO datetime is workspace time, not UTC; archive cascades to
  subtasks; a filter naming an unknown handle, tag or status returns an empty list, not an error.
- **One scheduler.** Due events are the recurrence scheduler's second hook, so they tick every
  60 s. The webhook dispatcher stays a separate lifespan task (~1 s, bus-woken). Rejected: running
  delivery as a scheduler hook (minute-scale latency).
- **Safe defaults, settings for the exceptions.** Where a deployment may reasonably want
  otherwise, Slate ships the safe default plus a setting (`SLATE_WEBHOOK_PRIVATE_TARGETS`,
  `SLATE_PUSH_ENDPOINT_HOSTS`), because Slate is meant to be run by anyone. Private webhook
  targets are off by default: a receiver on the same host needs the setting.
- **Authority and bounds.** The owner outranks admins for the owner's own tokens and account
  state; restricted callers cannot make workspace-wide automations; list-limited tokens cannot
  subscribe webhooks (refused rather than adding a `token_id` column); `dtstart` before 1970 and
  sub-daily BY-lists are refused; a rule spawns at most 100 items per tick.
- **Trash sets `archived_at` too** (when not already archived) so every "hide archived" path
  hides trashed items as well; restore clears `archived_at` only where the trash set it. Purging
  (by hand, Empty trash, or `SLATE_TRASH_DAYS` after trashing) deletes rows for good; events of
  purged items stay. Rejected: a separate trash table (a second copy of every item shape to keep
  in step).
- **Agent fast lane:** ETags from the max event id instead of per-row versions (rule 3 makes the
  event log a global version), batch built on the MCP action table instead of a second REST-path
  dispatcher (one catalogue, atomic through nested `apply_change()`), gzip level 1 (level 9 cost
  ~6x the CPU for a few percent). Rejected: an orjson dependency (pydantic's `dump_json` was
  already the fast part).
- **Batch views are built after the commit** (`views: "final"` default; `"each"` keeps per-op
  snapshots) because 80% of a batch's write-lock time was building views; the semantics are
  documented in `docs/agents/rest.md`. Event engines that keep a cursor (`push`, `automations`)
  commit the cursor once per page and right after each send (at-most-once is kept), and skip to
  the newest event in one write when nothing listens, instead of one extra SQLite writer
  transaction per engine per event. Rejected: a post-commit item-cache prefill from the batch's
  views (boards refetch little; the cache stays read-path only). Numbers in
  `docs/PERFORMANCE.md`.

### Webhooks, recurrence and automations

- **Webhook secrets are encrypted** (AES-GCM, `SLATE_SECRET_KEY`), not hashed, so Slate can sign.
  Webhook bodies use the same flat `event_payload()` shape as SSE plus `item` and `actor`: one
  shape everywhere. Delivery rows are guard-exempt bookkeeping. Retries = 1 attempt + one per
  configured delay (5).
- **Recurrence limits.** A rule never back-fills occurrences before its creation or resume day;
  sub-daily `FREQ` is refused; a rule ends through `PATCH {ended: true}` and cannot be un-ended;
  `run-now` materializes only what falls inside the rule's lead window (the same window and policy
  as the scheduler tick). `GET /api/v1/recurrences/{id}` exists for the MCP `get` action.
- **The recurrence preview's plain-English text is computed on the server** (`rrule_text.py`) so
  agents get the same words over MCP; the board's preset translation is client-side
  (`lib/rrule.ts`) because it is UI state. "Make recurring" adopts the open item
  (`adopt_item_id`) instead of starting the rule after it, so `skip_if_open`/`roll_forward` see it
  as the open instance.
- **Automation trigger `agent_reported {state?}`** (event `item.reported`): fires when an agent's
  badge changes state (working, blocked, review, done, idle); a new note in the same state is not
  a change, so a chatty runner does not re-run the rule. This is how a list moves a card to its
  own active column the moment an agent starts (the badge stays a flag, rule 4; the column move is
  the list owner's rule in their words), with no outside poller. A heartbeat repeating the current
  note/progress writes no event.

### Agents and the card conversation

- **Card conversation.** The agent's reply is a comment and the person's answer an Ask to the
  agent (the existing Ask machinery wakes it, scopes it and resolves it), rather than a new wake
  event type. An explicit Ask to the agent the card waits on is the reply (same as a plain
  comment), because the board's Ask picker starts on the card's agent and never lists the viewer.
  Thread refs live in their own table (per item and agent) inside the `item.reported` change. Bare
  heartbeats stay bookkeeping: the badge age is since the last report, not the last keep-alive, to
  keep History free of a row per minute. Rejected: a `waiting_on` push on every blocked report
  (the field change already notifies; `again` covers the repeat).
- **Clearing a review or blocked flag.** A person dismisses "Ready for review" with an `idle`
  report of their own (`POST /items/{id}/report`, `comment: false`; the board's Mark reviewed). A
  person's status change clears a review flag, and closing a card (done or cancelled) clears its
  review or blocked flag whoever closes it, an agent included (`lists._settle_agent_flag` ->
  `reports.clear_flag`, inside the same change; a working flag stays, and an agent's own move that
  does not close is left to its report). The Agents roster (`service/agents.py`) skips closed and
  trashed cards, the same rule as Needs you: something blocked or waiting for review shows in the
  left pane, and nowhere else.
- **Card chat answers every question.** An open Ask for an agent overrides `review`, waiting on
  someone and a done status in `work.eligible()` (like Run now), so a follow-up on a card the
  agent already answered wakes it at once instead of sitting unanswered. The board shows no "Asked
  X" pill on questions to agents and one "<agent> is typing…" line until the answer lands (pills
  stay for Asks to people).
- **One answer per turn.** `work.finish` posts no note or reply when the agent already reported
  the same outcome with a comment of its own since its last take (`_already_answered`: the turn
  starts at its report moving into `working`), so an agent that finishes through its own Slate
  tool and then has its runner report the same result posts one "Done", not two. An interim
  comment ("Looking now") does not count: only a matching report plus a comment.
- **`later` is its own finish, not a kind of `blocked`.** An agent that already has the go and
  must act at the due time finishes `later`: badge cleared, still assigned, nobody waited on,
  and the existing due gate and run stage bring it back. It needs a run time still to come
  (`work.check_later`, else 422) so it can never park a task nothing will wake; the runner turns
  that refusal into a fenced `blocked` on the owner. Rejected: reusing `blocked` (a false
  *Waiting on you*), `review` (asks a person to check unfinished work), and a new agent state or
  board column (rule 4: the due time already says when).
- **Run now by the agent itself wakes its runner.** `checkin._judge` drops an agent's own events
  except `item.run_requested` (plus reminders and digests), so another session of the same agent
  can file a card and press Run now and the runner takes it. The board does not say "Waiting — X
  is working on another card" (`lib/pickup.ts`): agents run several cards at once and a working
  badge may come from another session, so an unclaimed card shows "Waiting for pickup…" and then
  "Not picked up yet. Check agent status."
- **A parked badge is not a claim.** A `blocked` or `review` badge with no lease is not work in
  progress: `/work/next` still offers the card to an agent with an open Ask on it. Only a live
  lease or a `working` badge holds the card against an Ask (`work.eligible` `held_by_other`,
  `reports._check_claim`; `tests/test_work_ask_parked_badge.py`); treating another agent's parked
  badge as a claim for the 6 h `CLAIM_FRESH` hold would leave the Ask hanging.
- **A person can lift an expired run; nobody overrides a live lease.** A run that lost its lease
  leaves a quarantined lease that has to be reconciled before the card can be taken again. A
  person's Clear on the badge confirms the stop (it records a reconciled `item.work_finished` by
  that person and drops the lease), and an agent's own reconcile wakes its runner
  (`docs/agents-work-loop.md`).
- **An agent asked on someone else's card cannot park it on a third person.** An agent that is
  only asked on a card (not assigned) cannot mark it blocked or waiting on another person; its
  answer is just a comment. A blocked badge has a **✕ Clear** button that takes it off without
  touching the Waiting on field.
- **A comment that @mentions only people is a plain comment**, not an Ask to the card's default
  agent, so an agent never answers a message meant for a person.
- **A project's question goes to the project's agent.** The comment composer addresses the Ask to
  the first agent `@mentioned` in the text unless the viewer picked by hand
  (`conversation.mentionedAgent`). Otherwise the picker starts on, in order
  (`conversation.defaultAsk`): the card's agent assignee, the card's private-section agents (the
  project's own guest agent first, `board.projectAgents`), the agent last asked on the card, the
  flag owner, the viewer's last Ask (localStorage `slate.lastAsk`), the oldest push agent. The
  project's agents come before a remembered choice so that a project question cannot go to a
  different agent shared on the same project. The server honours exactly the `assignee` it is
  sent. A guest's board merges each private section's participants (`GET /sections/{id}/project`,
  which carries `role`) into its account map and reloads them on `section.participants_changed`,
  so guests see project members by name instead of "Someone".
- **Agent action deny-list.** One workspace rule (`workspace_settings` row `agents.denied`, default
  `delete`, `archive_structure`, `share`, `tokens`) enforced in `core.Change` for agent actors
  whatever their token; people are never limited; only an owner or admin person changes it
  (`settings.changed`). Chosen over a per-token policy so one switch covers every agent.
  `docs/permissions.md`.
- **AI add is built in, not routed through agents.** `POST /items/ai` files one item from plain
  words and the configured Ollama model fills it in (`service/aiadd.py`, settings
  `SLATE_AI_URL`/`SLATE_AI_MODEL`, an Ollama server, typically on the same host, so nothing leaves
  it and nothing is billed). It is for everybody, guests included. Rejected: handing the fill to
  an agent (that depends on a runner being up and on who can talk to which agent). Fills are
  written as the `slate` account (source `system`, `session_ref` `ai-add`); people offered as
  owners are only those the caller can see; fields the person changed first are kept; a model
  failure leaves the card as typed. The prompt carries a three-week calendar because the model
  gets relative dates wrong without one.

### Shared projects and chat

- **Every row written in a change must name its task for the scope gate.** An upload writes
  `Attachment` (has `item_id`) and `AttachmentBlob` (only `attachment_id`);
  `access.check_object` follows a blob to its attachment (pending in `session.new` or already
  stored) and checks that task's list, so a guest can attach files inside a shared project
  and is still refused outside it. Rule for new child tables: give the row an `item_id`, or
  teach `check_object` the one hop to it; the guest catch-all is a wall, not a default.
- **Members-only chat: a workspace agent is invisible to a project's guests.** Chat between
  members and a workspace agent (any non-guest agent) is members-only (`Message.members_only`,
  `docs/collaboration.md` "Members-only chat"). Chosen over removing the agent's share, so the
  agent keeps working the project for the members.
- **A project's guests never see a workspace agent.** The members-only rule covers every surface
  a guest reads (cards, history, live stream, pages' threads, pickers), and every way a guest
  could reach a workspace agent is refused (`service/guests.py`, `docs/collaboration.md` "Guests
  never see a workspace agent"). Cards a workspace agent made stay visible, because they are the
  work; their maker shows as nobody.
- **Shared project agents are tagged-only, not intelligent monitors.** `chat.post` supplies an
  implicit mention only for a channel agent whose role is not `guest`. Guest-role agents require
  an explicit mention on posts, threads and follow-ups; edits wake only added mentions. The
  existing mention events drive dispatch and the waiting/working indicator. `docs/chat.md`,
  `tests/test_project_agent_mentions.py`.

### Documents

- **Docs are their own tables.** Separate `docs`/`doc_versions`/`doc_files` tables, not items of a
  new kind: docs must stay out of boards, metrics, check-ins and automations. Autosaves fold into
  one version per author per 10-minute sitting so history stays readable. Images live in SQLite
  (`doc_files`, 10 MB cap), keeping one file to back up.
- **Files are docs, not a separate library:** a file doc is a `docs` row of kind `file` whose
  revisions are `doc_files` rows, in the same tree as pages, so a drawing sits next to its spec
  and one place answers "where is the thing we were talking about". Linear revisions, never
  deleted; restore is a new revision (the page-history rule). The note on upload is required for
  everyone (on a PDF it is the only diff). Bytes stay in SQLite (one file to back up) with 25 MB
  per revision and `db.blob_bytes` on the health report to watch growth. Not built: editing a file
  in place (a spreadsheet is edited in Excel and re-uploaded), external storage.
- **Doc comments are their own table** (`doc_comments`), not nullable `comments.item_id`: task
  Asks drive the agent work loop, check-in, inbox and Today, all of which assume an item; doc
  mentions and Asks reuse the `doc.mentioned` event, so every router that already handled a page
  mention handles a doc comment. Two surfaces (review threads anchored to a spot, resolvable, with
  Accept for a suggested replacement; discussion as the running conversation with activity folded
  in), the gesture choosing between them.
- **Compare is by type and never edits.** Spreadsheets and CSVs diff cell by cell with rows
  aligned by index (a re-saved sheet keeps its shape; this is not a merge), PDFs diff their
  extracted text and say so (`approximate`), images get a swipe, the rest only their notes. Text
  is extracted once per revision on upload and stored (`doc_files.text`) rather than on every
  search or compare. The extractors are `openpyxl` and `pypdf`: both pure Python, so one container
  and one SQLite file still hold.
- **Doc tags reuse the `tags` table** rather than a second taxonomy: tags cut across everything,
  and a word like `spec` should mean the same on a task and on the drawing it is about. The hub's
  "Needs you" is deliberately narrow (asks to me; others' open review threads on docs I touched)
  so it reads as a to-do, not a feed; the feed is the folded Recent activity under it. The CLI
  backfill (`slate backfill-doc-text`) runs as the system agent and records an event per revision,
  so "every change is an event" holds for a maintenance job too.
- **Drawers, not an overlay.** A hub that gathers every document of a section (every meeting
  transcript included) is the mistake; documents go where someone puts them. A drawer is a list
  kind rather than a new table because documents already carry `list_id`, lists already have a
  place in the sidebar, counts, sharing and chat, and "a section holds lists, drawers and pages"
  is a sentence a person can say. Folders are docs of kind `folder` so the tree code, moves and
  access rules carry over unchanged. Documents have no statuses; the pinned revision marks "what
  the work is on".
- **Doc pages are edited in place** (contenteditable, no editor library): people do not work in
  Markdown. Storage stays Markdown. `renderDoc(src, ctx, { split: true })` gives one block per
  construct with its exact `src` slice; `lib/richdoc.ts` `drawEditor` puts each in a
  `div.md-block[data-b]`, and `editorMarkdown` writes the screen back: a block whose content still
  serializes the same returns its original slice byte for byte, so typing in one paragraph never
  reformats an agent's page. Rejected: TipTap/ProseMirror (a runtime dependency plus custom nodes
  for every Slate token, and lossy Markdown round trips). Formatting uses `document.execCommand`
  (deprecated but supported everywhere, and keeps native undo).
- **A doc @mention is a turn.** A doc @mention of a push agent (page body, review or discussion
  thread, or a doc Ask) is a turn like a chat mention: `doc.mentioned_agent`, a `DocWake` lease,
  `doc` from `/work/next`, `POST /work/doc/finish` posts the reply as a comment; no task or item
  run starts (rule 7). The chat machinery is mirrored, not generalised into one
  table, so a runner author learns one shape (`docs/agents-work-loop.md` "Doc mentions wake an
  agent").

### The board and notifications

- **Comment dots on cards.** A card shows one dot, bottom right, when a comment is new to you
  since you last opened it: gray for agents only, blue for a person (guests count), pulsing blue
  when you are @mentioned or asked. Opening the card clears it; reading the Inbox does not. Stored
  as `item_reads` (account, item, `read_at`), written by `POST /items/{id}/read` as one private
  `item.read` event (subject = the account, no `item_id`, so not item history and not an
  item-cache invalidation); read by `GET /me/comment-dots`. Bottom right because the top right
  holds Complete and ⋯. `docs/board.md` "Comment dots", `service/item_reads.py`.
- **Priority stays in words; push severities live only in the bridge.** The board says
  Urgent/High/Normal/Low and quick add's `!1` is urgent. Rejected: showing priorities as numbered
  tiers (words read better). The optional `integrations/push/bridge.py` maps
  urgent/high/normal/low to severities 4/3/2/1 (no flag or no card: 2; never 5) and publishes one
  account's inbox to a push-notification broker; Slate knows nothing about the broker.
  `deploy/server/slate-push.service` runs it as a user unit.
- **Notifications: a waiting bell and a "Tell me when" grid.** The bell at the top right on
  desktop shows mention and message counts and a menu of what is unread; a seven-row grid
  (toast/push and email) stands for the sixteen notification reasons. Email goes through any SMTP
  server only when an item is still unseen five minutes later, batched per person. SMS is out of
  scope. `docs/notifications.md`.

### Repository checks

- **The private-terms denylist is never in the repository.** A SHA-256 of one lowercase word hides
  nothing: a name or a hostname is recovered by hashing guesses, so a committed list publishes
  exactly the words it exists to keep out. The list lives in the clone's Git directory
  (`.git/private_terms.sha256`, shared by its worktrees) or in the file
  `SLATE_PRIVATE_TERMS_FILE` names; CI writes it from the `PRIVATE_TERMS_SHA256` repository
  secret and skips the check where that secret is absent (pull requests from forks). CI's
  `private-terms` job hands the secret only to the base branch's copy of the checker, run with
  the system Python on the change's files and on each new commit's message and author and
  committer addresses; no code from the change runs in that job, so a change to the checker
  takes effect after it merges. Every workflow token is read-only, every checkout drops the token,
  actions are pinned by commit and Dependabot moves the pins (`tests/test_ci.py`). A clone
  without a list checks nothing; a list that is empty or malformed still fails. A hit in a CI log
  names the file and line, never the word. `tests/test_private_terms.py` fails if a `.sha256` list
  is tracked. After `python scripts/check_private_terms.py --add WORD`, update the secret with
  `gh secret set PRIVATE_TERMS_SHA256 < .git/private_terms.sha256`. A term is one word of
  letters and digits, compared without case or accents: `--add` refuses anything else and names
  the words to add instead (`my-host` is `my` and `host`), since a hash of a phrase never matches.
- **The private-terms check covers the bytes being committed.** The pre-commit hook scans index
  contents and filenames (including paths with spaces), not a potentially different working copy.
  The check also scans lockfiles, the license, Office XML and Office metadata. Commit-message
  checks include author and committer email addresses; public author names and GitHub's masked
  `users.noreply.github.com` attribution addresses are allowed. Run `sh scripts/install-hooks.sh`
  after cloning or changing the hook scripts; existing clones do not acquire hooks from a pull.
  The secret-scan workflow checks reachable history on pull requests and pushes to main and
  release tags. These checks are detection, not a guarantee: words that are not on the list,
  images, compressed PDF content and deployment details still need review, and CI runs after
  publication, so it cannot make an already-pushed secret private again. Use synthetic fixtures
  and generic deployment examples. Office fixtures must open in the board's reader: generate the
  synthetic workbook with `node web/e2e/fixtures/generate-workbook.mjs` and run the
  documents/drawer Playwright tests (a valid Office ZIP alone does not prove preview
  compatibility). Token examples in tests use visibly synthetic repeated characters, never
  captured credentials.

### Removed features

Custom fields, dashboards, goals, sprints, time tracking, forms, Slack and Discord notification
channels, email-to-task, the GitHub integration, task and status templates, duplicate merge, the
Workload page and the board's Import page are out of scope and are not to be rebuilt without a
maintainer decision. Where one of them had tables, its create-then-drop migrations remain in
`alembic/`, so a database that ran the creating migrations upgrades cleanly through the dropping
ones and a new database ends at the same schema.

## Gotchas

- Inside `apply_change()`, any query autoflushes. Change an object only *after* the queries you
  need, or pass it through `cx.*` first, or the flush sees an uncovered write (`WriteWithoutEvent`).
  Example: build a body version before assigning the new body.
- `lists.default_status_id` <-> `statuses.list_id` is circular: tests drop the schema with `PRAGMA
  foreign_keys=OFF` (see `conftest.py`).
- `tzdata` is an explicit dependency so `zoneinfo` works on Windows and slim images.
- The server is assumed to have Docker but **no Node**. Frontend builds happen in CI or inside the Dockerfile's build stage.
- For `web/` use Node 22 or later (CI uses 22); on a machine with several Node runtimes, pick the system one explicitly.
- The `client` test fixture and the `db` fixture share one in-memory SQLite (`StaticPool`);
  `fresh_schema` recreates it per test. Seed data through service functions; raw `engine.begin()`
  SQL is only for tests that probe database constraints.
- A test that writes through a bare `Session` will hit `WriteOutsideChange`. That is the guard
  working; use a service function or `apply_change()`.
- `Dropdown float` moves its panel under `<body>` (the `portal` action) and positions it with window
  coordinates. Inside its owner it would be trapped: a sticky table cell (`td.t-title`, z-index 2)
  is a stacking context, so later rows would paint over the ⋯ menu, and `.tw`'s `translate` makes
  it the containing block for fixed children, so the task window's Move picker would draw off the
  window. Locate a floating panel from `page`, not from the card or row. E2E visibility checks miss covered or
  clipped panels; `e2e/move.spec.ts` hit-tests with `elementFromPoint` at 1920×1080.
- Board select boxes (`Card`, `ItemRow`, `TableRow`): never `preventDefault()` a checkbox click. The
  browser then restores the old tick after the handler, Svelte thinks `checked` is already current,
  and the box stays blank. Let the click tick it and set `checked` from `board.selected` after
  `toggleSelect`. `.shell.selecting` (anything selected) shows every box, phones included. On a card
  the box is `.card-select`, a chip half outside the top-left corner (clear of the title, the
  "Subtask of" line and the subtask link at 16px down); `.cu-board .cards` has top padding so the
  column's scroll does not clip it on the first card, and the column head gives up the same. A
  card's ⋯ mounts on first focus, so an empty `.card-more-slot` holds its place; without it the
  first tap on a phone moves Complete under the finger.
- SQLite + many concurrent agents: WAL mode and a single writer connection pool
  (`check_same_thread=False`, pool size small). Long transactions are the enemy; keep service
  functions short.
- The board's `EventSource` listens to an explicit list of event types (`store.svelte.ts`
  `EVENT_TYPES`). A new event type is silently never heard until it is added there; the symptom is
  "the write succeeds, nothing live-updates". `tests/test_board_event_types.py` fails when the list
  is incomplete; add the type there in the same change as `docgen.EVENT_DOCS`.
- `apply_change()` holds the process write lock from the first write to the last publish, so never
  `await` or block on I/O inside it (`docs/live-stream.md` says why the lock exists).

## Topic docs (the rest of the internals)

Each feature's internals live in one file under `docs/`. The names in parentheses are the
section titles code comments cite as `TECHNICAL-GUIDE "..."`.

| Topic | File |
|---|---|
| Agents: reports, presence, the work loop and agent stream, Run now and the due gate, the runner, Asks, wake modes and Copy brief, the card conversation, chat mentions that wake an agent, the agent plan, pickup evidence ("Agent reports", "Agent work loop and agent stream", "Run now and the runner", "Webhooks and due events" for the run stage) | `docs/agents-work-loop.md` |
| Agent kit: onboarding, Add my agent, the generated reference, Python/TypeScript clients, the Slate skill installer, chat apps, agent connections and the portable install, the optional adapters | `docs/agent-kit.md` |
| Live event stream and polling: the write lock, the replay/subscribe seam, slow consumers ("Board", "Event types") | `docs/live-stream.md` |
| Webhooks and due events: design, signatures, retries, retention, targets | `docs/webhooks.md` |
| MCP server: the tool table, the two bindings, auth over HTTP, stdio, how to add an action ("MCP server") | `docs/mcp-server.md` |
| Recurrence engine and scheduler: rules, DST, the cursor, `materialize`, ClickUp options, preview and adopt, the hook list | `docs/recurrence.md` |
| Fast lane, batch and performance at scale: ETags, projections, `views: "final"`, `SerialCursor`, the item cache ("Item cache"; "Querying items" is above, in this file) | `docs/fast-lane.md` |
| The board's views and layouts: structure editors, the views engine, Board/List/Table/Calendar/Timeline/Box, favorites, dependencies and estimates | `docs/views.md` |
| Task window, drafts, palette and quick add, My Work, inbox, undo, one search, revert ("Task window") | `docs/task-window.md` |
| Design system, polish, phone and installed app: tokens, motion, icons, the build revision, PWA, touch drag, share target ("Scoped class names"; "The date rule" is above, in this file) | `docs/design-system.md` |
| Automations | `docs/automations.md` |
| Reminders, the daily digest, push notifications and delivery receipts | `docs/notifications.md` |
| Docs & Files: pages, file docs, drawers, threads, compare, facts, collaboration correctness, extraction caps | `docs/docs-and-files.md` |
| Trash, Archive page, export, backup, duplicates | `docs/trash-and-export.md` |
| Chat and agent chats | `docs/chat.md` |
| Permissions: roles, shares, private lists, limited tokens, the audit log | `docs/permissions.md` |
| Private shared projects and guest sessions | `docs/collaboration.md`, with `docs/shared-project-collaboration.md` and `docs/collaboration-security.md` |
| Health and observability | `docs/health.md` |
| The board's module map, store, reducer and test recipe | `docs/board.md` |
| Measured performance story | `docs/PERFORMANCE.md` |
| ClickUp importer | `docs/importer.md` |
| Release notes | `docs/RELEASE-NOTES.md` |
| Connect your own agent (user flow) | `docs/connect-your-agent.md` |
| Agent-facing docs (hand-written pages and the generated reference) | `docs/agents/` |
