# Slate: how it works

For an agent or a person picking this up with zero context: what Slate is, how it is built, the
rules every write obeys, how to run and deploy it, and the gotchas. It describes the product as
it is now, never how it got here. Feature internals live in the topic files indexed at the end;
keep this file an index plus the parts below, and update the topic file in the same pull request
as the change it describes.

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
            │  scheduler (asyncio tick): recurrence, due     │
            │  events, reminders, ...                        │
            │  engines (webhooks, automations, push, email)  │
            │    ◄── events table                            │
            └───────────────────────────────────────────────┘
```

- `service/` is the only module allowed to write to the database. Every write goes through
  `apply_change()`, which records the `events` row and publishes it to the in-process event bus.
  The bus feeds the SSE stream and long-poll waits, and rings a doorbell for the webhook
  dispatcher and the automations, push and email engines, which each read the events table after
  their own cursor.
- Routers and the MCP server are thin: validate input, call a service function, shape output.
- Background asyncio tasks start in `lifespan` and are registered with `monitor.track`: the
  scheduler (ten hooks on one tick: recurrences, due events, reminders, digest, trash purge, lease
  expiry, chat-turn expiry, doc-turn expiry, session-expiry notices, the sweep of unsent chat
  files), the webhook dispatcher, the automations engine, the push engine and the email engine; a
  thread warms the item cache. Every one must be idempotent on restart.

### Modules

`ls src/slate src/slate/service src/slate/api` is the list of record; this table says what each
one is for. Routers in `api/` are thin (validate, call a service function, shape output) and are
mounted in the order of `api.MODULES`; `api/events.py` and `api/recurrences.py` are mounted by
`app.py` through their own `install(app)`.

| Path | What it is |
|---|---|
| `app.py` | `create_app()`: the REST routers, the MCP app at `/mcp`, the middleware (fast lane, runner protocol check, and outermost the request body cap), `BoardFiles` (the built board, security headers), `/healthz`, `GET /health` (cached 10 s), `/docs` (Swagger UI pinned by version and SRI, under its own CSP; no ReDoc), the lifespan (MCP session manager, scheduler, dispatcher, automations, push, email, cache warm-up). |
| `settings.py` | `Settings` (pydantic-settings, `SLATE_*` env vars; every one is listed in `deploy/server/env.example`, and the root `env.example` is the short starter for a first run). |
| `db.py` | Engine, `SessionLocal`, `get_session`, the write-guard hooks on every `Session`, `SerialCursor` (`docs/fast-lane.md`), `SharedConnection` (the tests' one in-memory connection, used by one thread at a time: `docs/PERFORMANCE.md`). |
| `models.py` | Every table, SQLAlchemy 2 `Mapped` style, `UTCDateTime`, enums, the SQLite triggers (`TRIGGERS`). |
| `ids.py` | `new_id()` 12-char base62 keys; `new_event_id()` time-ordered ids for events and messages. |
| `dates.py` | `parse_when()` (the date rule), `local_day_bounds()`, `run_at()`, `is_date_only_input()`. |
| `crypto.py` | AES-GCM for webhook and automation secrets under `SLATE_SECRET_KEY`. |
| `rrule_text.py` | One RRULE in plain English (`describe`). |
| `scheduler.py` | The asyncio tick loop and `hooks`/`POLLERS` (`docs/recurrence.md`). |
| `notify.py` | Webhook dispatcher (HTTP side, `attempt`, `Dispatcher`) and the due/reminder/digest scheduler hooks. |
| `outbound.py` | The one outbound POST (webhooks, automation webhooks, Web Push): resolve once and connect to the checked address, one wall-clock deadline, capped drain; the signature headers. |
| `automator.py`, `pusher.py`, `emailer.py` | The automations, Web Push and email engines (`mailer.py` is the plain SMTP client; `service/email.py` decides who is emailed). |
| `engine.py` | The one bus-woken loop those three and the webhook dispatcher run; reports each step to Health. |
| `webpush.py` | Web Push protocol: VAPID, ES256 JWT, aes128gcm. |
| `fastlane.py` | `Timing`, GZip, `Conditional` (ETags from the event cursor, `CACHEABLE`). |
| `batch.py` | `/batch` and `slate_batch`: many MCP actions in one request, atomic by default. |
| `ratelimit.py`, `presence.py`, `monitor.py` | In-memory per-token buckets, who is around, what the Health page counts. |
| `liveactivity.py` | In-memory lines a run posts about what it is doing now; sent to boards as id-less `item.activity`, and kept with the run's answer when it finishes (`comment_runs`). |
| `mcp_tools.py`, `mcp_server.py` | The MCP tool table and the server (`docs/mcp-server.md`). |
| `docgen.py` | Generates `docs/agents/reference/` (`uv run slate docs`); `EVENT_DOCS`. |
| `build.py` | The build revision (`rev`, `commit`) on `/healthz`. |
| `cli.py` | `slate serve|migrate|docs|check-targets`. |
| `connection_models.py`, `runner_compat.py` | Agent connection rows; the runner protocol floor (426 for old runners). |
| `service/core.py` | `apply_change()`, `Actor`, the write guard, `BOOKKEEPING`, `event_payload()`. |
| `service/bus.py` | In-process pub/sub the stream, dispatcher and engines wake on. |
| `service/errors.py` | `NotFound`/`Conflict`/`Invalid`/`Forbidden`/`Gone`/`Unavailable` → 404/409/422/403/410/503. |
| `service/accounts.py`, `sessions.py`, `collaboration.py`, `connections.py` | Accounts and tokens; guest-session expiry notices; invitations and cookie sign-in; agent pairing. |
| `service/access.py`, `permissions.py`, `audit.py` | Roles, shares, token limits and the two enforcing hooks; the writes behind them; the audit log (`docs/permissions.md`). |
| `service/agent_rules.py` | The agent deny-list: a fixed vocabulary of actions agent accounts may never do, whatever their token or role (`check`, `check_read`); people are never affected. Served at `GET`/`PUT /settings/agents`. |
| `service/guests.py` | What a project's guests may do with the workspace's agents: guests reach only their project's agent, never a workspace agent, and do not see members-only chat. |
| `service/aiadd.py` | AI add: `POST /items/ai` files one item from plain words, then the configured Ollama model (`SLATE_AI_URL`) fills it in as `slate` after the response. |
| `service/sections.py`, `lists.py`, `tags.py`, `views.py`, `favorites.py`, `colors.py` | Structure, statuses (`move_item_to_status` decides `completed_at`), tags, saved views, favorites; the colour check for statuses and tags. |
| `service/items.py`, `item_cache.py`, `links.py`, `trash.py`, `export.py`, `search.py`, `revert.py` | Items and their views; the cached view JSON; dependencies and the derived blocked state; trash and purge; JSON/CSV export and the admin's whole-database backup copy; one search; revert from the log. |
| `service/comments.py`, `checklists.py`, `attachments.py`, `watchers.py`, `activity.py` | The task window's parts (`docs/task-window.md`). |
| `service/item_reads.py` | Comment dots: when each account last opened each item (`docs/board.md`). |
| `service/today.py`, `inbox.py`, `checkin.py`, `brief.py` | Today; the inbox derived from events; the agent check-in; the paste-ready brief. |
| `service/reports.py`, `work.py`, `agents.py`, `chatwake.py`, `docwake.py`, `turns.py` | Agent badges, the work loop and leases, the roster and plan, chat- and doc-mention wakes and their shared turn lease (`docs/agents-work-loop.md`). |
| `service/recurrences.py`, `due.py`, `reminders.py` | Rules and the materializer; due events and the run stage; reminders and the digest. |
| `service/webhooks.py`, `deliveries.py` | Subscriptions; the durable delivery queue and its prune. |
| `service/automations.py`, `push.py` | The automation engine step and vocabulary; push subscriptions and the push engine step. |
| `service/cursors.py` | Engine cursors and the event walk push and email share. |
| `service/chat.py` | Channels, messages, threads, reactions, exact read exposure (`docs/chat.md`). |
| `service/chat_files.py` | Files on chat messages: upload to a channel, send with a message, read where the message is read (`docs/chat.md`, "Files on messages"). |
| `service/docs.py` with `doc_tree.py`, `doc_pages.py`, `doc_files.py`, `doc_listing.py`, `doc_shape.py`; `doc_threads.py`, `doc_compare.py`, `doc_hub.py` | Pages, file docs and drawers, split by job (`docs.py` re-exports the public names); discussion and review threads; extract and compare; the project hub (`docs/docs-and-files.md`). |
| `service/workload.py`, `health.py` | Capacities and per-account workload numbers (`GET /workload/boxes`, for agents); the health report. |
| `api/auth.py`, `api/schemas.py`, `api/uploads.py`, `api/longpoll.py` | `require(scope)` and the `Auth` aliases; the core request/response models and the text-field limits; the app-wide request body cap (`BodyLimit`), counted raw upload bodies, and `file_response`, the safe response every stored file is served with; the long-poll helpers. |
| `api/*.py` (the rest) | One router per resource: accounts, connections, collaboration, permissions, sections, lists, items, comments, checklists, attachments, tags, today, views, favorites, inbox, chat, webhooks, links, stats, batch, agents, checkin, work, automations, reminders, docs, doc_comments, workload, data (export, backup, empty trash), health, search, settings (the agent deny-list); plus `events` and `recurrences`. |

## Schema notes

- **Ids** are 12-char base62 (`0-9A-Za-z`) strings made in Python. No `_`, so a token
  `slate_<id>_<secret>` splits cleanly. Event ids (and chat message ids) are time-ordered: 7 chars
  of milliseconds + 5 random. A per-process logical clock gives two ids in one millisecond
  consecutive logical milliseconds, and on the first connection it continues after the newest
  stored event and message id, so a restart never mints an id that sorts below a committed one.
  `apply_change()` assigns, commits and publishes ids under one write lock, so `ORDER BY id` is
  commit order and the event stream uses the id as its `since` cursor.
- **Timestamps** use `UTCDateTime`: aware UTC datetimes stored as fixed-width ISO 8601 text
  (`2026-10-23T17:00:00.000000Z`). Naive datetimes raise. Text order is time order.
- **Enums** are `VARCHAR` + `CHECK` (non-native), so a bad value fails in the database, not only in
  Python.
- **Rules held by the database:** tag names unique and lowercase (`CHECK name = lower(name)`); item
  depth ≤ 4 (a top task plus four levels of subtasks), no cycles, and no reparent that would push
  a subtree past four (triggers `items_depth_*`), and no item is its own parent
  (`ck_items_not_own_parent`); `external_ref` unique when set (`ux_items_external_ref`); an item
  link never points at its own item (`ck_item_links_not_self`) and is unique per
  `(from_item_id, to_item_id, kind)`; events append-only (triggers `events_no_*`);
  `(recurrence_id, occurrence_at)` unique, for idempotent recurrence spawning; a doc is never its
  own parent (`ck_docs_not_own_parent`) and its `kind` is `page`, `file` or `folder`
  (`ck_docs_kind`); doc link targets are `doc` or `task` (`ck_doc_links_kind`); doc comment
  surfaces are `review` or `discussion` (`ck_doc_comments_surface`). Plain unique columns and
  pairs (handles, slugs, `(list_id, name)` for statuses, version numbers) are in `models.py`.
- **Triggers are not autogenerated by Alembic.** They live in `models.TRIGGERS` (attached to
  `create_all` for tests) and are repeated verbatim in the baseline migration;
  `tests/test_migrations.py` fails if the two drift. A future migration that changes a trigger must
  drop and recreate it by hand.
- **The migration history starts at one baseline** (`alembic/versions/*_baseline.py`, revision
  `a7e2c9d4f1b3`, no parent). It creates the whole schema as plain SQL, the statements SQLite
  stored for a database that grew through Slate 0.1.0's step-by-step history, so the two schemas
  are the same. It keeps that history's last revision id, so a database at that revision
  or later needs nothing. A database at an older revision is refused by `alembic/env.py` with the
  advice to upgrade it with Slate 0.1.0 first. Later migrations chain after the baseline as usual.
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
    cx.record(
        "item.updated",
        item_id=item.id,
        field="tags",
        before=[...],
        after=[...],
        covers=[*removed_rows, *added_rows],
    )
```

`create` also takes `after=` (instead of the snapshot) and `covers=` (rows added alongside, such
as an item's first body version).
`cx.derive(obj, type, field=..., before=..., after=..., **values)` sets values and records an
event that follows from this change rather than an edit the actor asked for (an item unblocked
because its blocker was done), with no checks. Every other helper first runs `agent_rules.check()`
(the workspace's agent deny-list refuses an agent actor whatever its token) and the actor's access
check, so a refused write raises before anything is written.

The outermost block holds one process-wide write lock from its first write to its last publish.
On leaving it, it flushes, settles blocked/unblocked links for what changed (`links.settle`,
recorded as derived events), commits, invalidates the cached item views the events touch, then
publishes each event (`event_payload()` shape) to `service.bus.bus`. Any exception rolls back and
publishes nothing. If something commits or rolls back the session's transaction while a block is
open, the block raises `ChangeRolledBack` instead of committing the rest. Inner `apply_change()`
blocks join the outer one (same actor required), so service functions can call each other and still
commit once. Event payloads never include `token_hash`, `secret_hash`, `secret_enc` or the Web
Push keys `p256dh` and `auth` (`core.SECRET_COLUMNS`).

**The guard** (`core.guard_flush`/`guard_bulk`/`guard_cursor`, hooked onto every `Session` and
the engine in `db.py` so import order cannot skip it):

- any insert/update/delete flushed with no `apply_change()` open → `WriteOutsideChange`;
- inside a block, any written object not passed through `create`/`set`/`delete`/`record(covers=...)`
  → `WriteWithoutEvent`;
- ORM bulk `insert()/update()/delete()` outside a block → `WriteOutsideChange`; inside a block it
  must record at least one event;
- any other SQL write a session sends (`text()`, `session.connection().execute`,
  `exec_driver_sql`, `bulk_insert_mappings`), seen at the cursor by `guard_cursor`: the same
  rule as an ORM bulk write;
- an `Event` modified or deleted → refused (and the database triggers refuse it too).

The exemptions are bookkeeping, not changes to anything a person or an agent owns.
`core.BOOKKEEPING` lists single columns: `Token.last_used_at` (refreshed by `resolve_token()` at
most once a minute), `AgentConnection.last_seen_at` and `verified_at`,
`PushSubscription.last_ok_at` and `failures`, and `ChatWake.until` and `DocWake.until`.
`core.BOOKKEEPING_MODELS` lists whole tables that are operational logs or indexes: `Delivery`
(the webhook delivery log), `PushDelivery`, `EmailDelivery`, `AutomationRun`, `EngineCursor`,
`WorkLease`, and `DocLink` (an index of what a page body embeds; the body's event is the record).
A delivery attempt is a row, never an event, so attempts never flood the log or loop back into
webhooks. SQL on a bare `engine.connect()` connection, outside any session, is not guarded. Only
migrations and tests write through one (tests seed old rows with `conftest.raw_write`); the app
uses bare connections only for reads (`/healthz`, the newest event id for the stream cursor,
seeding the event id clock).

**How to add a mutation:** write a function in `slate/service/<area>.py` taking `(session, actor,
...)`, validate, then do every write through `cx.*` inside `apply_change()`. Then classify it in
`tests/test_no_write_without_event.py`: `MUTATIONS` with a scenario (or `None` when another test
checks its attribution), `READS`, or `INFRASTRUCTURE` (helpers that run inside a caller's change
or write as Slate's own account). The test walks every public function in `slate.service` and
fails on anything unclassified or on a stale entry, fails if a function listed under `READS` opens
`apply_change()`, and runs each mutation scenario asserting every event it left carries the
caller's actor, source and `session_ref`.

**Event types.** The full list, one line each, is generated into `docs/agents/reference/events.md`
from `docgen.EVENT_DOCS` (`uv run slate docs`); `tests/test_agent_docs.py` fails when a type in code
has no line or a line has no code. Naming: `<subject>.<what happened>`, where the prefix names what
the event is about (usually a table's singular, such as `item.`, `doc.`, `account.`) and the rest
is usually a past-tense verb (`item.created`, `doc.moved`); scheduler and state events name the
stage instead (`item.due_soon`, `item.overdue`, `health.alert`, `doc.agent_turn`). `item.updated`
carries `field` (`title`, `due_at`, `body_md`, `tags`, `completed_at`...); `item.assigned` and tag
changes carry the whole before/after handle or name list; events about a non-item row put its id in
`subject_id`. The board hears only the types listed in `lib/board/events.ts` `EVENT_TYPES`
(`tests/test_board_event_types.py` keeps that list complete).

## Core REST and the item rules

All under `/api/v1`. Reads need `read`, writes `write`, administration (tokens for other
accounts, accounts, permissions, agent pairing, data and settings) `admin`. A few writes that only
touch the caller's own credential (revoking a token or connection, signing out) need only `read`,
and `POST /batch` checks each op's scope. Service errors map to HTTP in `api/__init__.py`
(`_STATUS`): `NotFound` 404, `Conflict` 409 (something already exists or is held: a slug, tag,
handle, `external_ref`, a work lease), `Invalid` 422 with a plain-English `detail` (rule
violations: depth, stranded statuses, bad dates, undo window), `Forbidden` 403 (the caller's role,
token or ownership does not allow it), `Gone` 410 (bootstrap after the first run, a used pairing
code or invitation), `Unavailable` 503 (a feature switched off by configuration); any other
service error is 400, and a failed batch takes its cause's status. The body is
`{"detail": ...}`, plus `failed_op` for a batch and `existing_id`/`existing_state` for a taken
`external_ref`. Pydantic shape errors are FastAPI's usual 422 list.

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
  on leaving; entering done or cancelled also clears `waiting_on_account_id`, and entering done
  makes the next occurrence of a recur-when-done rule. A person's move clears a review flag, and
  closing the card clears a review or blocked flag whoever closes it. `set_statuses()` uses the
  same function for items it moves, and re-stamps items on a kept status whose kind crosses done.
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
internal callers such as recurrences pass `due_date_only` themselves), and
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
| `kind` | `task` (the only item kind: documents are docs, not items) |
| `include_archived` | default false hides archived items **and** items in archived lists/sections; trashed items stay hidden |
| `archived` | true: only archived items (not trashed); false: only unarchived |
| `trashed` | only items in the trash; without it trashed items are never returned, even with `include_archived` |
| `closed` | true: only done/cancelled; false: only open/active (or no status) |
| `blocked` | true: has an open blocker (dependency); false: has none |
| `stale` | not done/cancelled, and either (a) >=3 `item.updated` events on `due_at` whose previous value was set (first-time setting is not a push), or (b) in an `open`-kind status (or none) with no event of any kind for >=30 days (comments count as touches) |
| `changed_since` | an event id (for example a previous `X-Slate-Cursor`): only items with an event after it |
| `ids` | comma-separated item ids, at most 1000: only these items |
| `open_comment_for` | handle or id: only items with an open Ask addressed to that account |
| `order` | `position` (default), or newest first by `completed`, `archived`, `trashed`, `updated`, `created` |
| `fields` | comma-separated item fields to return (`id` always); default all (REST only) |
| `limit`, `offset` | a page of the ordered result; `limit` 1 to 1000 |

Unknown handles, tags or status names match nothing (empty list), not an error. Order: `position`,
then `created_at`, then `id` by default; `order=completed|archived|trashed|updated|created` sorts
newest first by that date (items without it last). Pagination is `limit` (1 to 1000) and
`offset` (`docs/fast-lane.md`). New items get `position = max + 1` in their list. `views()` turns
rows into output dicts with a fixed set of batch queries (statuses, assignees, tags, subtask
counts, links…), never one per item. Measured: 10,000 items in
5 lists, `GET /items?list_id=&tag=` returns 176 rows in ~11 ms median through the test client
(`tests/test_perf.py`, marker `perf`; skip with `-m "not perf"`).

**`external_ref` is unique when set** (partial unique index `ux_items_external_ref`): one card
per thing an outside filer tracks. `service/items.py` `_claim_ref` checks
inside the change scope (the write lock makes check-then-write race free; the index is the backstop)
on create, split, PATCH and bulk update, and raises `errors.ExternalRefTaken` (a `Conflict` carrying
`existing_id`). The API's 409 body is `{"detail": "external_ref already used by <id>[, which is
closed: reopen that card instead of filing a new one]", "existing_id": "<id>", "existing_state":
"open|closed|archived|trashed"}`; MCP returns the same text as the error. Closed, archived and
trashed items keep their reference (a recurring incident reopens its card, it does not
get a new one), and the detail tells the filer to reopen, unarchive or restore it. A blank reference
is stored as null. Recurrences never copy `external_ref`.

**Today (`GET /today`, `service/today.py`):** open items the caller can see (not done/cancelled,
not archived, not in an archived list or section) in three groups, each item in the first that
fits: `overdue` (due before now), `today` (due before midnight workspace time), `waiting`
(`waiting_on_account_id` is the caller). Each sorted by priority (urgent, high, normal, low,
none), then soonest due. A fourth group, `asked`, holds open items with an open Ask addressed to
the caller, oldest Ask first; an item can be in `asked` and in one of the three. Also returns
`now` and `day_ends_at`; `fields` trims items as on `GET /items`.

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
`api/__init__.py`; (6) add an API test; a core route goes into `CORE_ROUTES` in
`tests/test_openapi.py`, and a new `GET /items` filter into `ITEM_FILTERS` (it must equal the
route's query parameters); (7) to expose it to agents, bind the handler as an action in
`mcp_tools.py` (`tests/test_mcp_parity.py` checks params and scope); (8) run `uv run slate docs`
(and add any new event type to `docgen.EVENT_DOCS`), or `tests/test_agent_docs.py` fails.

## Auth

- Token plaintext `slate_<token id>_<secret>` (secret = 32 random bytes, urlsafe). Stored:
  `sha256(plaintext)` hex only. The plaintext appears in a response body only from
  `POST /bootstrap`, `POST /accounts/{id}/tokens` and `POST /agent-connections/exchange`; an
  accepted invitation sets it as the HttpOnly `slate_session` cookie instead.
- `resolve_token()` parses the id, loads that row, constant-time compares the hash, and rejects
  malformed, expired and revoked tokens and disabled accounts. `authenticate()` wraps it for every
  door (REST, the live stream, MCP) and accepts a credential from the `slate_session` cookie only
  if it is a browser-session token.
- **Scopes are ranked** `read < write < admin` (`accounts.SCOPE_RANK`); a token's effective level
  is its highest scope, except that `admin` counts only on an owner or admin account and never on
  a list-limited token (such a token works as `write`). `require("read")` accepts write and admin
  tokens.
- `require(scope)` takes a Bearer token, or with no `Authorization` header the `slate_session`
  browser-session cookie (a write through the cookie must be a same-origin board request, else
  403). It answers 401 (with `WWW-Authenticate: Bearer`) for a missing, invalid, expired or
  revoked token; 403 for too low a scope; 429 with `Retry-After` and `X-RateLimit-*` headers when
  the token is over its per-minute read or write limit. Returns `Auth(principal, actor)`;
  `auth.actor` goes to service writes.
- **`session_ref`**: optional `X-Slate-Session-Ref` header (≤200 chars) on any request; lands in
  every event that request writes.
- **Bootstrap:** `POST /api/v1/bootstrap {handle, display_name}` with no auth, only while no
  account other than `slate` exists (checked again under the write lock): creates a `person`
  account with the `owner` role and an `admin` token, and seeds the default views (the new account
  is the actor of its own events). After that: 410.
- `DELETE /tokens/{id}` (needs only `read`): an account revokes its own tokens, and an admin
  revokes any member's or guest's token; tokens on an admin or the owner account are revoked only
  by the owner or that account. A list-limited credential can revoke only itself. Revoking twice
  is a no-op 204.
- **The board keeps a pasted token in `localStorage`.** The browser has to hold the credential
  somewhere, and the live stream can only send it as a query parameter (EventSource sends no
  headers; the server redacts `token=` from the access log). Guests are different: an accepted
  invitation mints a browser session held in an HttpOnly cookie (`docs/collaboration.md`). A
  pasted token is not exchanged for one, so it stays readable by any script on the origin until
  **Sign out**. The board never writes it to a URL it builds, a link or a log line; the only
  exception is the connect-an-agent dialog, which shows a new token once, on purpose, so it can
  be copied into the agent.
- **The owner outranks admins.** Only the owner (or the account itself) mints or revokes tokens on
  an admin or the owner account, or renames, disables or enables it (`accounts._require_outranks`:
  an admin who could mint another admin's token would act as them, and one who could disable them
  would lock them out).
- Handles: `^[a-z][a-z0-9_-]{1,31}$`. `slate` is reserved for Slate's own account, which takes
  no tokens. Bootstrap is refused once any account other than `slate` exists (a scheduler alarm
  before the first sign-in may create `slate`).

## Run / test / build

See `AGENTS.md` → Commands. Python is managed by uv (`uv python install 3.12`); `uv sync` creates
`.venv`.

Each runtime dependency in `pyproject.toml` has its floor set to the version `uv.lock` pins and a
cap below the next major (the next minor for SQLAlchemy, 1.0 for a 0.x package; `tzdata`, dated
data, has no cap). A change that
moves the lock raises the matching floor too; `tests/test_portable_package.py` builds the wheel and
installs it outside the repo with the lock's versions as constraints.

## Deploy

The step-by-step procedure for a long-running server is `deploy/server/RUNBOOK.md` (service user,
`.env`, first deploy, https, bootstrap accounts, verify, update, rollback, logs, restore drill, and
the optional push-notification and AI add setups). The
examples use the service user `slate`, install dir `/opt/slate` (repo `/opt/slate/repo`, data
`/opt/slate/data`, env `/opt/slate/.env`) and `https://slate.example.com`; put your own values in
the server's `.env`, not in the repo.

- **Build on the host.** The server clones the repository (a private fork needs only a read-only
  deploy key) into `/opt/slate/repo` and `docker compose -p slate up -d --build` builds the image
  there; Node exists only inside the Dockerfile's build stage and uv (pinned) only inside its
  Python stage: the runtime stage copies `/app` and runs as uid 10001. CI publishes no image: nothing
  pulls one, and a publish job would need a token that can write packages.
- **Files:** `deploy/server/compose.yml` (project `slate`, container `slate`,
  `restart: unless-stopped`, `127.0.0.1:18448:8000`, `/opt/slate/data:/data`,
  `env_file` `/opt/slate/.env`, `init`, 20 s stop grace, json-file logs 10m×5, a `/healthz`
  healthcheck every 30 s, `host.docker.internal` mapped to the host gateway for AI add, run as the
  service user's uid and gid with every capability dropped and `no-new-privileges`,
  forwarded headers trusted from loopback and Docker's bridge pools `172.16.0.0/12` and `192.168.0.0/16` only, never `*`); `slate.service` (user unit, oneshot + RemainAfterExit, reload = rebuild);
  `slate-backup.service/.timer` + `backup.sh` (daily at 03:30; `backup.py`, fed to the container's
  Python on stdin, takes the SQLite online backup, refuses a missing or non-Slate database and
  streams the copy out; `backup.sh` checks its SHA-256 and keeps 30 days in `/opt/slate/backups/`,
  which the container does not mount, and records each run in `data/backup-status.json` for the
  Health page); `deploy.sh [ref]` (idempotent install or update for any `SLATE_HOME`: needs
  `.env`, refuses a checkout with local changes, backs up a running database to
  `backups/pre-deploy-<stamp>.db` before the restart, waits for `/healthz` to serve the new
  commit, adds Tailscale Serve when `SLATE_TAILSCALE_PORT` is set); `rollback.sh <ref>` (refuses
  a ref without the database's migration revision, backs up a running database to
  `backups/pre-rollback-<stamp>.db`, rebuilds from the ref and waits for `/healthz` to serve it);
  `env.example` (every setting); `slate-push.service` (optional user unit for the
  push-notification bridge in `integrations/push`; not installed by `deploy.sh`).
- **Exposure:** the container publishes on loopback only; put a TLS reverse proxy (Tailscale Serve,
  Caddy, nginx) in front of `127.0.0.1:18448`, on a private network unless you decide otherwise.
- **Portable install:** the root `compose.yaml` (project `slate-portable`) runs the same image on
  `127.0.0.1:${SLATE_PORT:-8000}` with a `slate-data` named volume and no systemd units or backup
  timer; it needs `SLATE_SECRET_KEY` in `.env` and is independent of `deploy/server`
  (`docs/agent-kit.md`, "Agent connections and portable installation").
- **`/healthz`** (unauthenticated) answers `ok`, `version`, `db` ("ok" after a `SELECT 1`),
  `scheduler_last_tick_age_s` (null when the scheduler is disabled or has not ticked),
  `webhooks_enabled` (`SLATE_SECRET_KEY` set) and `min_runner_protocol` (the oldest runner
  protocol the server accepts) for deploy checks and clients; `rev`, `commit` and `board` carry
  the build (`docs/design-system.md`, Build revision). Queue
  counts are on the authenticated health report, not here; `db_path` is on the authenticated `GET
  /api/v1/stats`.
- **Gotchas:** every `docker compose` call passes `-p slate` (a test enforces it in
  `deploy/server/`). The container `CMD` runs `slate migrate`, then `exec`s `slate serve` (uvicorn
  in-process), so SIGTERM reaches the server rather than the shell.
  `deploy.sh`/`rollback.sh` wrap their body in `main()` so `git checkout` rewriting the running
  script is harmless. systemd eats `$(`; unit files write `$$(`. Rolling back past a migration needs
  a backup restore (the schema does not downgrade): `rollback.sh` refuses a ref that lacks the
  database's revision before it changes anything. `deploy.sh` and `rollback.sh` succeed only when
  `/healthz` serves the commit they checked out. `tests/test_deploy.py` lints all of this locally
  (`bash -n`, unit keys, `docker compose config` when Docker is up) and runs the backup, deploy and
  rollback scripts under bash against stand-ins for `docker`, `systemctl` and `curl`.

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
- SQLite + many concurrent agents: WAL mode, one process write lock, and a connection pool that
  never makes a caller wait (`db.POOL_SIZE` kept open, more opened on demand). `require()` hands
  the request's connection back once the token is checked, and long-polls end their read before
  they wait, so a request holds a connection only while it queries. Never hold a session's
  transaction open across an `await` on anything but the database. Long transactions are the
  enemy; keep service functions short.
- The board's `EventSource` listens to an explicit list of event types
  (`web/src/lib/board/events.ts` `EVENT_TYPES`). A new event type is silently never heard until it is added there; the symptom is
  "the write succeeds, nothing live-updates". `tests/test_board_event_types.py` fails when the list
  is incomplete; add the type there in the same change as `docgen.EVENT_DOCS`.
- `apply_change()` holds the process write lock from the first write to the last publish, so never
  `await` or block on I/O inside it (`docs/live-stream.md` says why the lock exists).

## Topic docs (the rest of the internals)

Each feature's internals live in one file under `docs/`. The names in parentheses are the
section titles or bold paragraph labels that code comments cite as `ARCHITECTURE "..."`.

| Topic | File |
|---|---|
| Agents: reports, presence, the work loop and agent stream, Run now and the due gate, the runner, Asks, wake modes and Copy brief, the card conversation, chat and doc mentions that wake an agent, the agent plan, pickup evidence ("Agent reports", "Agent work loop and agent stream", "Run now, the due gate and the runner") | `docs/agents-work-loop.md` |
| Agent kit: onboarding, Add my agent, the generated reference, Python/TypeScript clients, the Slate skill installer, chat apps, agent connections and the portable install, the optional adapters | `docs/agent-kit.md` |
| Live event stream and polling: the write lock, the replay/subscribe seam, slow consumers ("Live event stream and polling"; "Event types" is above, in this file; the board's side is `docs/board.md`, "Live stream and catch-up") | `docs/live-stream.md` |
| Webhooks and due events: design, signatures, retries, retention, targets | `docs/webhooks.md` |
| MCP server: the tool table, the two bindings, auth over HTTP, stdio, how to add an action ("MCP server") | `docs/mcp-server.md` |
| Recurrence engine and scheduler: rules, DST, the cursor, `materialize`, ClickUp options, preview and adopt, the hook list | `docs/recurrence.md` |
| Fast lane, batch and performance at scale: ETags, projections, `views: "final"`, `SerialCursor`, the item cache ("Item cache"; "Querying items" is above, in this file) | `docs/fast-lane.md` |
| The board's views and layouts: structure editors, the views engine, Board/List/Table, favorites, dependencies and estimates | `docs/views.md` |
| Task window, drafts, palette and quick add, My Work, inbox, undo, one search, revert ("Task window") | `docs/task-window.md` |
| Design system, polish, phone and installed app: tokens, motion, icons, the build revision, PWA, touch drag, share target ("Scoped class names"; "The date rule" is above, in this file) | `docs/design-system.md` |
| Automations | `docs/automations.md` |
| Reminders, the daily digest, push notifications, delivery receipts, email notifications and the waiting bell | `docs/notifications.md` |
| Docs & Files: pages, file docs, drawers, threads, compare, facts, collaboration correctness, extraction caps | `docs/docs-and-files.md` |
| Trash, Archive page, export, backup, duplicates | `docs/trash-and-export.md` |
| Chat, files on messages and agent chats | `docs/chat.md` |
| Permissions: roles, shares, private lists, limited tokens, the audit log, the agent action deny-list | `docs/permissions.md` |
| Private shared projects and guest sessions | `docs/collaboration.md`, with `docs/shared-project-collaboration.md` and `docs/collaboration-security.md` |
| Health and observability | `docs/health.md` |
| The board's module map, views, store and reducer, live stream catch-up, writes, comment dots, quick-add grammar and test recipe | `docs/board.md` |
| Repository checks: the private-terms check, the hooks, CI rules, how rule 5 is tested | `docs/repository-checks.md` |
| Measured performance story | `docs/PERFORMANCE.md` |
| Release notes | `docs/RELEASE-NOTES.md` |
| Connect your own agent (user flow) | `docs/connect-your-agent.md` |
| Agent-facing docs (hand-written pages and the generated reference) | `docs/agents/` |
