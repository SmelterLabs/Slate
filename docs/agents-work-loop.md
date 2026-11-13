# Agents: reports, the work loop, Run now, Asks and the card conversation

How an agent takes work from Slate and talks back. The agent-facing version (which calls to
make) is `docs/agents/`; this is the server side. Related: `docs/agent-kit.md` (onboarding,
clients, the skill), `docs/chat.md` (chat), `docs/notifications.md` (push).

## Agent reports, presence, roster and rate limits

- **Reports** (`service/reports.py`, `POST /items/{id}/report`, MCP `slate_item report`): an agent
  says `working | blocked | review | done | idle` with an optional `note` and `progress`. The
  current flag lives in `items.agent_state/_by/_at`, `agent_note`, `agent_progress` (migration
  `7a9e4c1d2b30`) and every report is one `item.reported` event (`after = {state, note, progress,
  by}`), so history has every report. It is a flag beside the status, never a status (rule 4).
  `done` clears the flag and, with `complete` (default), moves the item to the list's first done
  status; `idle` clears it only. `blocked` + `waiting_on` sets `waiting_on_account_id`; `assign_me`
  adds the reporter. A note is also posted as a comment (`comment=false` to skip); the board's feed
  folds that echo away (`agents.svelte.ts` `isReportEcho`).
- **Presence** (`slate/presence.py`): in memory only. `auth.require()` marks `api`, the MCP local
  backend marks `mcp`, the SSE stream counts open streams per account. After a restart it falls back
  to token `last_used_at`. Never persisted: it is bookkeeping, not a change, so it has no event and
  `/agents` is not in `CACHEABLE`.
- **Roster** (`service/agents.py`, `GET /agents?kind=agent|person|all`, MCP `slate_agents`): one row
  per account with presence, what it is on (items whose flag it set), open assignments, events in
  the last 24 h (index `ix_events_actor_id_id`) and how often the rate limit refused it.
- **Per-actor events:** `/events`, the stream and `slate_events` take `actor` (handle or id).
  `/events` also takes `actor_kind=person|agent` and `tail=true` (the newest `limit` events,
  returned oldest first); the board's Agents page seeds its feed with `tail=true&actor_kind=agent`
  and then follows its one stream.
- **Rate limits** (`slate/ratelimit.py`): a token bucket per token, separate for reads and writes
  (`SLATE_RATE_LIMIT_READS_PER_MINUTE` 1200, `..._WRITES_PER_MINUTE` 300, burst = one minute's
  worth). REST answers `429` with `Retry-After` and `X-RateLimit-Limit`; MCP returns a tool error
  with the wait. Every authenticated REST answer also carries `X-RateLimit-Limit`/`-Remaining` for
  the bucket it spent from: `auth.require()` leaves the `Verdict` in `request.state.ratelimit` and
  `fastlane.Timing` copies it onto the response start. A `304` is answered by `Conditional` before
  auth, so it spends nothing. `limiter.requests` counts requests per token and kind for the usage
  endpoint. In memory, per process. Off in tests (`tests/conftest.py`).
- **Check-in** (`service/checkin.py`, `GET /checkin`, MCP `slate_checkin`): the agent's poll,
  separate from the people-facing Inbox below. `queue` (open items assigned to or waiting on the
  caller) plus `news` (others' events that concern it, with `why`) from one cursor. SQL first keeps
  only candidate events (assignment, mention and waiting-on events, and anything on an item the
  caller holds or watches), then `checkin.why` judges each in Python against the caller's *current*
  assignments and watches, in pages of 500 (at most 10,000 candidates a call, then `has_more`). The
  call snapshots the newest event id first; when nothing more up to it concerns the caller,
  `next_since` jumps to it, so a cursor never rereads and other people's busy boards cost an agent
  nothing. `item.created` is never news (its `item.assigned` follows). In `CACHEABLE`: both halves
  are event-backed.
- **Claim** (`report(..., claim=True)`, REST/MCP `claim: true`, with `working` only): an atomic
  take. Inside `apply_change()` (so under the write lock) it refreshes the item and raises
  `Conflict` (409) when another account's flag is younger than `reports.CLAIM_FRESH` (6 h, the age
  the board dims a badge); it implies `assign_me`. Two agents racing for one item cannot both win.
- **Check-in queue cap:** `queue_limit` (default 50, 0 = news only) and `fields` (as on `/items`);
  `queue_total` says how many there are. `checkin.queue()` sorts on `(id, priority, due_at)` rows
  and loads only the returned items, and the route serialises with `api.checkin.dump()` (trims the
  queue items) straight to a `Response`. Without the cap an agent holding 400 items would get
  about 333 KB per check-in.
- **Hand to an agent** (`task/HandOff.svelte`): one atomic `POST /batch` of `item assign` (adds the
  agent) and `item comment` (`@agent <instructions>`). No endpoint of its own: the agent sees it
  through its inbox, stream or webhook.
- **Board:** `AgentBadge.svelte` (card and task window), `AgentsPage.svelte` at `#/agents` (a page,
  not a scope: `parseRoute` returns `page: 'agents'` and the selected scope is left alone), store
  `lib/agents.svelte.ts` fed by the board store's `#apply`. The badge dims a `working` flag older
  than six hours.

## Agent work loop and agent stream

- **Work loop** (`service/work.py`, `api/work.py`, MCP `slate_work`): `POST /work/next` takes the
  best free task for the caller, `POST /work/{id}/heartbeat` keeps it, `POST /work/{id}/finish`
  reports back, `GET /work` lists what the caller holds. A take is an ordinary claim
  (`reports.report(state="working", claim=True, comment=False)`), so the board needs nothing extra:
  `item.reported` + `item.assigned` events. On top of it sits a **lease** in `work_leases`
  (migration `5b7d2e9a4c18`; one row per held item: account, `until`, `from_pool`). The lease is
  bookkeeping (`core.BOOKKEEPING_MODELS`, like token `last_used_at`): a bare heartbeat moves `until`
  with no event, so an agent beating every minute does not flood the history; a heartbeat with
  `note`/`progress` is a real `working` report, unless it repeats the note and progress the badge
  already shows (then it is bookkeeping too: a runner that re-sends the same note every few seconds
  would otherwise bury the card's history under identical rows).
- **Which task:** `work.eligible()` is one SQL select (outer join on the lease). Scope = assigned to
  or waiting on the caller, plus (with `pool`) items with no assignee; restricted callers only get
  lists they may edit (`access.EDIT`). Skipped: not a task, closed, archived (or its list/section),
  `links.blocked_item_ids()` (the same "open blocker" rule as the item view; the dependency thread
  keeps it the source of truth), `start_at` in the future, `waiting_on` someone else, `review`
  badge, the caller's own `blocked` badge younger than `BLOCKED_PAUSE` (1 h, so a failing task is
  not retried in a loop), and held by another account (a live lease; with no lease, a badge younger
  than `reports.CLAIM_FRESH`, except that a parked `blocked`/`review` badge never holds a card
  against an open Ask for the caller; `reports._check_claim` applies the same rule at the take).
  Order: own before pool, priority, due (nulls last), position, created. **Every NOT branch tests
  for NULL first**: `NOT (lease.account_id = me)` is NULL for an item without a lease and would drop
  the row.
- **Races:** take, renewal, expiry and finish share the mutation lock. WorkLease/ChatWake carry
  per-attempt run_id; direct report guards and proof checks prevent same-account duplicates and
  foreign takeover. Tests exercise late finish, renewal-versus-expiry and lost result-response
  retries.
- **Resume (protocol 3):** a caller must retain the opaque run_id from its original take and supply
  it with the exact task/channel/thread. Same account or session_ref is not proof. A second process
  cannot adopt a lease or working badge automatically.
- **Finish (protocol 3):** run_id binds settlement to one attempt. Event-backed receipts preserve
  task-vs-Ask intent and accept only an identical retry; changed payloads and stale attempts
  conflict. Finish and taking next are separate. Direct reports cannot steal or clear active
  protected execution; optional handoff/status still occur inside the proven finish transaction.
- **Expiry (protocol 3):** renewal and expiry re-read under the same write lock. Expired task/chat
  attempts remain quarantined as unknown remote state, not freed for replay. Explicit work/chat
  reconcile requires verified native stopped/completed evidence and a reason. Chat reconcile uses
  the original agent identity; never clear a local fence blindly. A **person's Clear** on the
  expired badge (`POST /items/{id}/report` `state=idle|done`, no run_id) is that confirmation from
  a human: `work._person_clears_expired` records `item.work_finished` (reconciled, by the person,
  `evidence` "<handle> cleared the expired run's badge") and drops the quarantined lease. It never
  touches a live lease, and an agent account still needs its run proof. The agent's own reconcile
  is a self-wake on its stream (`api/checkin._judge`), like its own Run now: Asks that arrived
  while the card was fenced hinted nothing, so without it the runner would never come back.
- **Long-poll** (`api/longpoll.py`): `wait_async` (REST: subscribe to the bus first, attempt, on
  nothing roll the session back so the next attempt reads a fresh WAL snapshot, wait for any bus
  message, drain the burst, attempt again) and `wait_sync` (MCP local calls run in a worker thread:
  `bus.seq` + `bus.wait_sync`, a `threading.Condition` notified on every publish). `/work/next` and
  `/checkin` take `wait` (≤ 60 s; MCP ≤ 25 s to stay under the stdio bridge's 30 s HTTP timeout).
- **Agent stream** (`GET /checkin/stream`, in `api/checkin.py`): built on `api/events.payloads()`
  (the `/events/stream` replay-then-live generator, factored out; same permission gate,
  `EventGate`). Each visible event goes through `_judge` in the threadpool: `checkin.why()` (on a
  `SimpleNamespace` built from the payload, `work.as_event`) gives a `news` message; an `item.*`
  event (minus comments, watches, reminders and due nudges: `work.may_free_work`) whose item
  `work.is_takeable` for the caller gives a `work` hint. Both carry `id` = the event id, so
  `Last-Event-ID` resumes. During a replay a task is hinted once. The caller's own events are
  skipped (except reminders/digest). Counts as a live stream on the Agents page.
- **MCP gotchas:** nested pydantic models in tool params put `$ref`s in the merged input schema, so
  `slate_work finish`'s `next` is a plain object validated into `WorkNext`; and a `ValidationError`
  escaping a local action surfaces as a bare JSON-RPC "Invalid request parameters", so build
  response models from raw values, never from an already-dumped dict.

## Run now, the due gate and the runner

Slate stays the doorbell: it never launches an agent.

- **`item.run_requested`** (`work.request_run`, `POST /items/{id}/run` 202 → the event, MCP
  `slate_item run`): actor = who pressed, `after = {for: [agent account ids], reason: "now"}`, field
  `run_requested_at`. 422 with no agent assignee, or done/cancelled/archived. Also sets
  `items.run_requested_at` (migration `8c1f4a7e2d90`), which `work.take()` clears: "a Run now newer
  than the last run".
- **Due gate** (`work._run_time_has_come`, inside `eligible()`): when the caller is an agent, a task
  assigned to it with a `due_at` is takeable only once its run time has come or `run_requested_at`
  is set. Run time = `dates.run_at(due_at, due_date_only)`: 06:00 in the workspace timezone on the
  day for a date-only due date (`items.due_date_only`), else `due_at` (a task explicitly due at
  17:00 runs at 17:00). SQL cannot do timezones, so the condition is `due_at <= now OR
  (due_date_only AND due_at = dates.early_date_only_due(now))` (the one 17:00 instant whose 06:00
  has passed, between 06:00 and 17:00 local). Not gated: tasks with no due date, people, pool and
  `waiting_on` tasks.
- **`later` finish** (`work.finish`, `outcome: "later"`, closing line `SLATE: later`): the agent
  did what it can now and the rest must happen at the due time, with any go it needs already
  given. It reports `idle` (badge cleared), keeps its assignment, sets no `waiting_on` and
  completes nothing; the due gate then holds the task until its run time, and the run stage
  wakes the agent again. `work.check_later` refuses it (422) unless the task's run time is still
  to come, because otherwise nothing would run it again (or the gate would hand it straight
  back); `waiting_on` and `hand_to` are refused with it. The resident runner turns a refused
  `later` into the same fenced finish `blocked` on the owner with a note, so the answer is kept
  and a person sees the task needs a time. This is the honest ending for "scheduled, nothing
  needed from you": `blocked` there puts a false *Waiting on you* on the person's card.
- **Run stage** (`service/due.py`): for an item with agent assignees, `pending_stages` adds a stage
  `run` at `run_at - 1 µs` (so it sorts before `due` when they coincide) that records
  `item.run_requested` by `slate` with `reason: "due"` (and `due_at`). It rides on
  `items.last_due_event` like the other stages, so it fires once per due date and starts over when
  the date moves. It is skipped (nothing written; the `due` stage then moves the cursor past it)
  when one of the item's agents has taken or reported on it since the run time: assigning an agent
  after 06:00 already wakes it, and a second request on the card would read as a duplicate run.
  The candidate window is at least 11 h (`RUN_LEAD`) ahead.
- **Hints** (`api/checkin._judge`): `item.due` and `item.run_requested` are not in
  `work.NO_HINT_TYPES`; they are `work.WAKE_TYPES`, hinted every time (the live `hinted` set
  otherwise hints a task once per connection, which would swallow a second Run now). `due_soon` and
  `overdue` stay news.
- **`/work/next` `item_id`** (REST, MCP `slate_work next`): narrows `eligible()` (and the resume
  query) to that item, so a runner answering a hint takes the pressed task or gets `item: null`.
- **`url`** on reports, heartbeats and finish (`reports.check_url`: absolute http(s), ≤ 2,000):
  stored in `items.agent_url`, in the view's `agent.url`, in the `item.reported` `after.url`, and on
  the Agents roster. A report without `url` from the same agent keeps the link; `done`/`idle` and
  lease expiry clear it. A heartbeat with a new url is a `working` report (event), not bookkeeping,
  so the board shows the link live (rule 6); the same url again is bookkeeping.
- **Board**: `store.runRequested` (item id → ms) tracks an explicit press or `item.run_requested`,
  cleared by a non-idle report. `pickup.ts` also derives waiting for already-due tasks after reload
  and distinguishes visible work elsewhere from an unexplained wait; see “Due-task concurrency and
  pickup evidence” below. The same `RunNow.svelte` status appears on cards and beside the
  task-window status. `AgentBadge` renders `url` as "Open ↗" (`target=_blank rel=noopener
  noreferrer`, http(s) only).
- **Runner** (`clients/python/slate_runner.py`, docs/agents/runner.md): one file, `httpx` + stdlib,
  copyable like `slate_client.py`. **One runner per machine**: `--config runner.toml`
  (`load_config`, `serve`) runs one `Runner` per `[[agent]]` in one process, each with its own
  stream thread, token, env (`env_file`, `env`, `${VAR}`), cwd (the config folder) and cursor file;
  a refused token retries every 5 min without stopping the others. Hermes Agent profiles on a
  multiplexing gateway are `HERMES_API_URL=.../p/<profile>`. Adding an agent is a config block,
  never a new service.
- **Connection evidence:** recent account activity/SSE is not executor readiness. Guided connections
  use an explicit probe and private live checks; a runner without a guided connection is labelled
  unverified rather than claimed ready. Runner startup/reconnect refills after the SSE ready
  marker; proof-bound durable state prevents duplicate launches. Definitive 401/403/409 stops
  local work; transient transport errors retain uncertainty and retry delivery, not execution.

**Runner protocol.** `src/slate/runner_compat.py` holds `MIN_RUNNER_PROTOCOL`;
`clients/python/slate_runner.py` holds `RUNNER_PROTOCOL` and sends it as `X-Slate-Runner`. Any
change that breaks older runners (a field they send is removed or renamed, a response they read
changes shape) raises both in the same PR and bumps `__version__` to `0.<protocol>.0`; an older
runner then gets 426 on every call instead of silent per-call 422s (without it, a removed field
leaves a stale runner's finishes refused one call at a time). `tests/test_runner_compat.py`
fails if the server floor passes the shipped runner. The runner is also a package
(`clients/python/pyproject.toml`, `slate-runner` command, `uv tool install
"git+...#subdirectory=clients/python"`); the server package does not depend on it.

## Assigned comments ("Ask")

Task Asks notify a person in Inbox and push through the `mention` preference,
without an @mention, watch or task assignment. Comments never resolve them; `keep_open` is
compatibility-only. Earlier task commenters and Ask recipients receive `reply` notifications
from `item.commented.after.reply_to` (ids only). Clear/read affects notifications, not the Ask.
Agents assigned to a task must explicitly resolve outstanding Asks as well as report completion;
the asked-only `work/finish done` operation explicitly answers that Ask.

An Ask is a comment addressed to one account, open until explicitly resolved; independent of the
item's assignees and `waiting_on`.

- **Model** (migration `3e6a9d2b7f41`): `comments.assignee_id` (indexed), `resolved_at`,
  `resolved_by_id`. **Service** `service/comments.py`: `add_comment(assignee=, keep_open=)`,
  `assign_comment` (author or admin; assigning reopens), `resolve_comment` (assignee, author or
  admin; idempotent), `open_asks(account, item_id=)`. Events `comment.assigned` (`after = {assignee:
  handle, assignee_id, comment_id}`, null assignee when taken off) and `comment.resolved` (`after =
  {comment_id, by, reason}`). Comments, including agent report notes, leave Asks open until explicit
  resolution; `keep_open` is compatibility-only.
- **REST/MCP**: `POST /items/{id}/comments {body_md, assignee, keep_open}`, `PATCH /comments/{id}
  {body_md?, assignee?}` (`exclude_unset`: absent leaves it, null takes it off), `POST
  /comments/{id}/resolve`, `GET /items?open_comment_for=<handle>`; MCP `slate_comment
  add/assign/resolve`. Item views carry `open_asks` (assignee ids of open Asks, oldest first): the
  card's "?" count for the viewer is computed on the board, so the cached view stays
  viewer-independent.
- **Queues**: check-in news `asked` (`checkin.why`, `comment.assigned` added to the SQL pre-filter),
  `/today` group `asked` (oldest Ask first; may overlap the other groups), My Work card "Asked of
  you" (client side, `mywork.askedOfMe`).
- **Agents**: `comment.assigned` is a `work.WAKE_TYPES` hint. `work.eligible()` includes pending,
  unconsumed Ask assignments (why `asked`), even on done cards. Taking an asked-only item does not
  assign the agent (`reports.report(no_assign=True)`, lease `from_pool` false); `finish done`
  answers instead of completing (`complete=False`), explicitly resolving only its taken Asks. Every
  agent finish consumes its take's dispatch boundary, not visibility. `/work/next` returns only the
  pending `asks` within that take; new/reassigned Asks wait for the next turn.
- **Board**: Composer "Ask…" select (people and agents) turns Send into "Ask <name>";
  `ActivityPanel` shows "Asked X · Open/Resolved" (`data-ask`) for all Asks, plus Resolve for the
  assignee or author. Typing uses the work-finish receipt, not merely unresolved state. Reducer
  refetches on Ask/work events. E2E: `web/e2e/ask.spec.ts` and `conversation.spec.ts`.

### Ask dispatch and settlement (protocol 3)

Task Ask dispatch and resolution are separate. `item.work_started` records the take's ordered
event boundary; `item.work_finished` consumes assignments through it for that agent/item.
Every finish outcome consumes dispatch, preserving waiting/review/release semantics without
closing or hiding open Asks. Assigned-task done leaves them open; asked-only agent done
explicitly answers and resolves, scoped to the take. New/reassigned Asks during the run remain
pending. Proof-bound resume/heartbeat never moves the boundary. Protocol 3 carries the execution
proof in runner/client payloads. The board uses these same receipts to stop typing indicators
while retaining visible Open/Resolve pills. The event log is authoritative; do not prune these
records independently of task history.

## Wake modes, Copy brief, the `SLATE:` line and the adapters

The agent-facing version is `docs/agents/pull-agents.md`.

- **`accounts.wake`** (`push` | `pull` | `manual`, default `push`; migration `6b2d8e4f1a73`): set on
  create/PATCH (`accounts.WAKE_MODES`, an `account.updated` event) and on the Agents page
  (`agents/AgentWake.svelte`, with the saved prompt for `pull`). `GET /agents` rows carry `wake`.
  Nothing in core branches on it except who a run request notifies.
- **Nudge** (`work.nudge_fields`): `item.run_requested` (Run now, or the 06:00 run stage in
  `service/due.py`) carries `nudge: [{id, name, wake}]` and `notify: [account ids]` when an agent
  assignee is `pull` or `manual`. `notify` is each agent's `prefs.owner` (a person's handle), else
  the workspace owners. It is read before the write, because a query inside `apply_change` would
  flush the item uncovered. Inbox reason **`nudge`** (`inbox.reason_for`; push text "<Agent> has a
  Slate task waiting: open <Agent> and say “work Slate”"); the presser never gets their own.
- **Board**: `board.runMode(item)` is `run` if any agent assignee is `push`
  (`board.accounts[id].wake`), else `nudge` if any is `pull`, else `brief`. Card, menu and task
  window say Run now / Nudge / Copy brief (`RUN_TITLE` in `lib/runmode.ts`). A nudge shows "Nudged.
  Open <Agent> and say “work Slate”." (`data-run-state="nudged"`, no "nobody picked it up"). Copy
  brief puts `GET /items/{id}/brief` on the clipboard.
- **Brief** (`service/brief.py`, `GET /items/{id}/brief?ask=`, MCP `slate_item brief`): the title,
  due date (date-only aware), description, a `Done means:` block with `acceptance_md` verbatim (only
  when it is non-empty; `/work/next`'s `item` carries `acceptance_md` too), checklists, the last 10
  comments, then `RUN_RULE`: check your memory and past sessions first, save what's worth
  remembering at the end (an agent runtime may run each card in a new session, so the agent's own
  memory is the only bridge to its other chats), and the `SLATE:` closing line. `ask` defaults to
  the caller if it's a person, else the first owner (`default_ask`).
- **The `SLATE:` line** (`brief.closing_line`, applied in `comments._apply_closing_line`): only when
  `add_comment(closing_line=True)`, which REST and MCP (`slate_item comment`, `slate_comment add`)
  pass. Slate's own comments (report notes) never do, so a note can't re-trigger. An agent author is
  reported through `reports.report(state, comment=False, complete=<assigned>)`. A person gets
  `done` → the list's done status (`reports.done_status`) and `blocked waiting_on X` →
  `waiting_on`; `review` changes nothing.
  `Invalid`/`Conflict` are swallowed after the comment commits. An unknown handle drops
  `waiting_on`.
- **Adapters** (`integrations/`, rule 5): `hermes/run_task.py` for Hermes Agent (one gateway
  `api_server` session per card, see below) and
  `runner-example/run_task.py` (a worked example for an agent service reached over HTTP: one
  idempotent `POST /runs` per take, then `GET /runs/{id}` until it ends). Each folder's README
  holds the facts and the unverified points. Tests next to them (loaded by file path) run in CI
  as `uv run pytest -q integrations`. Nothing in `src/` or `tests/` imports them.

## The card is the conversation

Slate holds only generic pieces; everything runtime-specific is in `integrations/`.

- **Thread reference** (`models.AgentThread`, table `agent_threads`, migration `9d4f7b2c6e18`): one
  row per (item, agent), `ref` (opaque, ≤ 200) and `url`. `report`/`heartbeat`/`finish` take
  `thread: {ref, url?}` (`reports.check_thread`; a plain dict in the schemas, because a nested model
  would put a `$ref` in MCP tool params). A new or changed thread rides on the `item.reported` event
  (`after.thread`, the row in `covers`); the same thread again on a heartbeat is bookkeeping.
  `thread.ref` becomes the actor's `session_ref` (only at the outermost change:
  `reports.with_thread_ref`, since a nested `apply_change` must keep its actor). `thread.url` is the
  badge link when no `url` is given. Item views carry `agent_threads`; `/work/next` answers `thread`
  (the caller's). The board keeps the live row's `agent_threads` current from `item.reported`
  `after.thread` (`reducer.ts`, replacing that agent's row), and the task window renders them as
  `task/ThreadLinks.svelte` ("Open My Agent's session ↗", same `safeAgentUrl` scheme rule, hiding a
  link the badge already shows): a done run clears the badge but not the thread, and a reply reopens
  that same session, so the link must outlive the badge.
- **Runtime model:** there is no per-card model setting. Adapters preserve the runtime/profile
  default and existing session settings. Fresh gateway task/chat sessions receive the explicitly
  configured `HERMES_CWD`.
- **Open link schemes** (`settings.link_schemes` / `SLATE_LINK_SCHEMES`, default
  `["http","https"]`): `reports.check_url` accepts a `url` or `thread.url` only with a listed
  scheme, and every scheme still needs a host part (`hermes://chat/<id>?profile=my-agent` passes,
  `hermes:chat` does not). `GET /me` carries `link_schemes`, and the board
  (`conversation.safeAgentUrl`, used by `AgentBadge`) renders an Open link only for a scheme the
  server named (http(s) when it named none), so a desktop app's link is followed only where Slate
  was configured for it; a non-http(s) link opens in the same tab (the OS hands it to the app)
  instead of `_blank`. `javascript`, `data`, `vbscript`, `blob` and `file` are never
  accepted (`settings.UNSAFE_LINK_SCHEMES`): the setting refuses them at start-up, and
  `check_url` and `/me` drop them even so. Slate still knows nothing about Hermes: the scheme is configuration on the
  server, and the Hermes adapter (`integrations/hermes/run_task.py` `thread_ref`) is what sends
  `hermes://chat/<session id>?profile=<profile>` (profile from `HERMES_PROFILE`, else the
  `/p/<profile>` tail of `HERMES_API_URL`), which Hermes Desktop's `hermes://chat` deep link opens
  on the owning profile. `HERMES_SESSION_LINK=0` keeps the ref without the url for a Slate that
  lacks the scheme (a refused url is a 422 on the whole report).
- **Reply** (`report`/`finish` `reply`, ≤ 20,000): one plain comment by the agent instead of the
  labelled `**Blocked** · note`; the badge note is `note` or the reply's first line.
- **Reply turns** (`comments.reply_target`): a comment by the person an agent's `blocked` badge
  waits on (`waiting_on_account_id` = the author, `agent_state_by` an enabled agent, the author not
  an agent, no explicit assignee) is addressed to that agent as an Ask (`comment.assigned` with
  `after.reply = true`) and clears `waiting_on` in the same change. The Ask does the rest: it is a
  `WAKE_TYPES` hint (the agent stream adds `comment_id` and `reply` to the hint), puts the task in
  the agent's scope, ends the blocked pause (`eligible()`: an open Ask for you skips
  `BLOCKED_PAUSE`), and `/work/next` returns the newest person-written open Ask as `reply`. Replies
  leave it open until explicitly resolved.
- **Mid-turn reporting (protocol 3):** a resident runner owns heartbeat/finish. Its child must not
  independently report review/done or start another work loop. Reports during a protected attempt
  need the matching proof; a missing lease is never recreated from a badge. Definitive control loss
  stops local execution and preserves unknown remote state for reconciliation.
- **Run now after the last report** (`eligible()` `pressed`: `run_requested_at > agent_state_at`)
  overrides `waiting_on` someone else, the `review` badge and the blocked pause. **Run now while
  working** is 409 (`work._refuse_if_working`: a live lease, or a fresh working badge without one)
  with "My Agent already has it (working 4 min)". `take()` keeps `claimed_at` while the same
  account holds the lease, so that age is the run's, not the last heartbeat's.
- **Waiting on you**: the `waiting_on_account_id` change is inbox reason `waiting`. A
  blocked report that waits on the same person again (no field change) carries `after.waiting_on`
  and `after.again = true` and is the `waiting` entry instead (`inbox.reason_for`; the inbox
  pre-filter uses `json_extract(after_json, '$.waiting_on')`). The push reads "<Agent> needs you on:
  <title>" with the agent's note when the item is blocked by the event's actor.
- **Author kinds**: comment views carry `author_handle` and `author_kind` (`person`, `agent`,
  `guest` = a person with the guest role; `comments.authors_of`), so adapters can keep non-person
  comments out of the user's turn (the prompt-injection boundary is the adapter's).
- **Board**: `lib/conversation.ts` (pure, unit-tested): `isChat` (agent comments and comments
  addressed to an agent), `collapsedChat` (every exchange but the newest collapses; an exchange
  closes with an agent comment; an open Ask never collapses), `badgeLabel` ("Waiting on Alex",
  "Answered"), `updatedAgo`/`shortAge`. `ActivityPanel` renders chat comments
  (`data-chat="agent|person"`), collapsed rows (`data-chat-collapsed`) and the live bubble
  (`data-live-bubble`, own 1 s clock while an agent works). Runs of `item.reported` working events
  fold into one history line (`activity.ts foldable`). Run now stays offered while an agent works so
  the 409 is said (`store.runNow` flashes the server's message). Badge time: since the last report,
  which every heartbeat with a note refreshes; a bare keep-alive heartbeat stays bookkeeping (no
  event), so it does not reach the board.
- **Adapters**: `slate_runner.py` forwards `reply` and `thread` (result and progress lines). Hermes
  uses one `api_server` session per card, a watchdog (`/health` start time, session 404, gateway
  silent for `HERMES_DOWN_GRACE`) and approvals as Asks; facts and unverified points in
  `integrations/hermes/README.md`. The runner example sends the answer on the card as the next turn
  and reports the agent's page as the thread.
- **No `SLATE:` line is never `review`**: `review` means the agent says the work is finished, so
  a reply without the line must not send a card to review while its agent is still waiting on
  background subagents. The gateway adapter asks again in the same session up to `HERMES_NUDGES`
  (default 2) times (wait for background work, then end with the line); still no line, or the
  CLI/runner-example adapters: `blocked` waiting on the ask with a "Paused" note, so the card
  reads *Waiting on Alex* (never working, never review) and a reply continues the same session.
  The brief's `RUN_RULE` also tells agents to wait for background work before answering.
- Tests: `tests/test_card_conversation.py`, `tests/test_clients.py` (runner reply/thread),
  `integrations/hermes/test_hermes_adapter.py`, `web/src/lib/conversation.test.ts`,
  `web/e2e/conversation.spec.ts`.

## Chat mentions wake an agent

A person's `@mention` of an agent in a channel or thread starts its runner, the way an Ask on a
card does; the reply lands in the chat.

- **Model** `models.ChatWake` (table `chat_wakes`, migration `a7c3e9f1b2d4`): one row per (agent,
  channel, thread; `thread_id` is `""` for the channel itself, since NULL cannot be in the key).
  `message_id` = the newest unanswered mention, `since_id` = the oldest, `taken_id` = the mention a
  take covers, `claimed_at`/`until` = the lease. `(ChatWake, "until")` is in `core.BOOKKEEPING`, so
  a heartbeat moves the lease with no event; every other write to the row rides on an event. The row
  is scoped by its channel like a message: `access.read_criteria` filters it by the caller's visible
  channels and `check_object` needs `comment` on the channel.
- **Service** `service/chatwake.py`, hooked from `chat.post` / `chat.edit` inside their change
  (`on_posted`, `on_mentioned`). Wake only for mentioned accounts that are enabled agents with `wake
  == push` and may post in the channel (`access.compute` for the agent, under
  `access.unrestricted`), and only when the poster is a person: an agent's mention of an agent never
  wakes it (no loops; it still gets its inbox entry). One `message.mentioned_agent` event per message
  (`after.for` = agents woken, `after.queued` = agents whose turn there is already open: the mention
  folds into it, no second run). `pull`/`manual` agents are not woken; the mention reaches their
  inbox like any other.
- **Chat execution (protocol 3):** work/next returns chat plus chat.lease.run_id. Heartbeat and
  finish require exact channel/thread/proof. A plain chat post does not settle a fenced execution.
  Receipts make identical finish retries safe and preserve mentions arriving during the run.
  Timeout/lease expiry is unknown, not remote cancellation; capabilities restrict admission before
  taking unsupported work.
- **Stream** (`api/checkin._judge`): `message.mentioned_agent` with the caller in `for` or `queued`
  is a `work` hint `{channel, thread_id, message_id, queued}` (no `item_id`); `work.CHAT_WAKE_TYPE`
  names it so `api/checkin` stays free of chat imports. The board hears both event types
  (`EVENT_TYPES`); its chat store refetches the subject message, which is harmless.
- **Runner** (`clients/python/slate_runner.py`): a chat turn is keyed `chat:<channel>|<thread>` in
  the same reservation sets as item ids (`chat_key`, `task_key`), so parallel workers, `pending` and
  `dispatch` need nothing extra; a `queued` hint names a running key and is dropped there. The command
  gets `chat` on stdin and `SLATE_CHANNEL`, `SLATE_THREAD_ID`, `SLATE_CHAT_URL`; `_chat_result` maps
  the result line to `/work/chat/finish` (`done`+`reply` posts; anything else, a failure or no
  result line releases with a note, never posting stdout). `docs/agents/runner.md` "A chat turn".
- **Adapters:** the gateway adapter supports task and chat; CLI/runner-example declare task-only
  and refuse unsupported work. The local setup command passes supported work kinds into the
  runner. Native approval remains in the runtime, observed through terminal completion; no keyword
  authorization or guessed settlement route.
- Tests: `tests/test_chat_wake.py` (wake record, queue-into-open-turn, agent-mentions-agent, wake
  modes, permission rule, edit, own-post ends, release, heartbeat/expiry, 409s),
  `tests/test_agent_stream.py` (the SSE hint, queued, the reply), `tests/test_clients.py` (runner:
  channel, thread from a hint, failed run posts nothing).

## Doc mentions wake an agent (doc Asks)

The chat design applied to pages, not a third machinery: a person's `@mention` of an agent on a
page (a save that adds the handle) or in one of its review/discussion threads (a comment that
mentions it, or an Ask addressed to it) is a turn the runner takes; the answer lands on the page.
`doc.mentioned` is recorded as for any mention, so the inbox and check-in list it.

- **Model** `models.DocWake` (table `doc_wakes`, migration `d8e4a1c7f3b9`): one row per (agent,
  doc, thread; `thread_id` = the thread's first comment, `""` for a body mention). Where chat
  compares message ids, a doc mention has no row of its own for a body save, so the row counts
  instead: `mention_no` goes up per mention, a take sets `taken_no`, and `pending` = `mention_no >
  taken_no`. `comment_id`/`excerpt`/`by_id` are the newest mention's. `(DocWake, "until")` is
  bookkeeping; the row follows the doc's visibility (`read_criteria`) and needs `comment` on the doc
  (`_COMMENT_MODELS`, `check_object`'s doc branch).
- **Service** `service/docwake.py`, called from `docs._record_mentions` (body) and
  `doc_threads._record_mentions` (comments, Asks, `assign_comment`) inside their change, right after
  `doc.mentioned`. Same wake rule as chat: enabled `push` agents that may comment on the doc (their
  own `access.compute`), and never when the poster is an agent. One `doc.mentioned_agent` event
  (`after.for`, `after.queued`, `thread_id`, `comment_id`, `excerpt`). Repliers named only by
  `reply_to` (thread participants) are not woken: only a mention or an Ask is.
- **Turn** `POST /work/next`: tasks, then chat, then doc (`kinds` default `task,chat,doc`);
  `{doc_id, thread_id}` takes exactly that turn. `doc` carries the page (`doc_id`, `title`, `kind`,
  `path`), `section_md` (the heading section around `@handle` for a body mention, around the root's
  `anchor_quote` for an anchored thread; at most 8000 chars, `docwake.section_around`),
  `body_chars`, `comments` (the thread), `mention` (`excerpt`, `by_*`, the comment), `may` (`reply`;
  `edit` when the agent can edit a page) and `lease` with `run_id`. Events `doc.agent_turn` `state`
  started / done (`reply_id`) / released / expired (actor `slate`, `reconciliation_required`).
- **Answer** `POST /work/doc/finish {doc_id, thread_id, run_id, outcome, reply}` is the chat contract
  with `doc_id` for `channel`: `done`+`reply` posts the agent's comment in the thread, or a new
  discussion thread for a body mention; release posts nothing; identical retries return the receipt.
  `/work/doc/heartbeat` and `/work/doc/reconcile` likewise. As in chat, the agent's own comment does
  not end a fenced turn (chat's unfenced `_answered` path is unreachable under protocol 3, so docs
  have no counterpart). MCP `slate_work doc_heartbeat|doc_finish|doc_reconcile`.
- **Stream**: `doc.mentioned_agent` with the caller in `for`/`queued` is a `work` hint `{doc_id,
  thread_id, comment_id, queued}` (`work.DOC_WAKE_TYPE`). Scheduler hook `docwake.expire` (Health
  key `doc_turns`).
- **Runner**: key `doc:<doc_id>|<thread>` (`doc_key`, `turn_ref` maps chat and doc keys to their
  routes); `doc` on stdin, env `SLATE_DOC_ID`, `SLATE_THREAD_ID`, `SLATE_DOC_URL`; the result maps
  through `_chat_result`. `SLATE_WORK_KINDS` defaults to `task,chat,doc`; a server that does not
  know the `doc` kind answers 422 on `/work/next`, and the runner drops `doc` and retries. The
  gateway adapter declares `doc` and runs it through `run_chat` with `doc_prompt` (page text,
  thread and mention quoted as data; its own session per doc thread); CLI and the runner example
  release it. `slate_connect` gives gateway connections `doc`.
- **Board**: the reply is an ordinary `doc.commented`, so the discussion shows it live. There is
  no "is working" indicator for docs.
- Tests: `tests/test_doc_wake.py`, `tests/test_clients.py` (runner doc turns, the 422 fallback),
  `integrations/hermes/test_hermes_chat_adapter.py` (doc prompt, sessions, guest quoting).

## Agent plan: "about to"

`GET /agents/plan` (read; `api/agents.py`, `service/agents.plan`; MCP `slate_work plan`) answers
`{horizon_hours: 24, agents: [{id, handle, display_name, next: [...], leases: [...]}]}`, agents with
the soonest work first. It is the "about to" view: what each agent
will run in the next day, from the work loop's own rule rather than a looser copy of it.

- **Reuse, not a second rule.** `next` is `work.eligible(session, agent, now=until)` with `until =
  now + 24 h`: the same select `/work/next` runs, evaluated a day ahead, so the due gate
  (`_run_time_has_come`, 06:00 workspace time for a date-only due date, the due time otherwise),
  `run_requested_at`, pending Asks, waiting-on, the review badge, the blocked pause and other
  agents' leases all apply exactly as they will. Each entry's `reason` is `run_requested` (a Run now
  newer than the last report; `at` = the press), `ask` (an open Ask the loop has not handed over:
  `work.pending_asks`, the public face of `_pending_asks`; `at` = when asked) or `due` (`at` =
  `dates.run_at`, pushed later by a future `start_at` or the agent's own blocked pause; null `at` =
  no due date, takeable now). `leases` is `work.held_by` plus the title and `age_seconds`.
- **Reach.** Agents come through `access.participant_ids`; items through the read filter and
  `eligible()`'s scope rule, which for a restricted caller is the lists it may *edit*
  (`work._scope_lists`), the same as the work loop. So a view-only guest sees the agents in its
  projects with empty `next`. Note the plan is computed with the caller's access, not the agent's:
  an agent that is itself a guest may be shown tasks its own token could not take.
- **Board.** `AgentsPage.svelte` renders a "Next up" block above the live feed (`lib/plan.ts`:
  `planWhen` says `now`, `6:00 AM` or `Wed 6:00 AM`; `REASON_LABEL`; `heldFor`), re-read 400 ms
  after any `item.*`, `comment.*` or `account.*` event through `board.onEvent` (the audit log's
  pattern), never polled.
- **Digest.** `reminders.digest_for` adds `agents` ("Agents today": the plan until the end of the
  local day, `local_day_bounds`, computed under the recipient's access, at most `DIGEST_ITEMS` per
  agent) only when some agent has something; a digest with only agent news is still sent, the push
  text appends "Agents today: n tasks", and `access.digest_snapshot` drops the block for restricted
  readers (it keeps only the three item groups, fail-closed). `DigestOut.agents` defaults to `[]` on
  the preview.

## Due-task concurrency and pickup evidence

The runner's generic default is serial **per agent**. A deployment can give one agent more slots
and keep another serial. An assignment and due event are eligibility, not proof of execution:
actual pickup writes a working report and the adapter binds a separate thread to that card. Keep
dependent work ordered with dependencies rather than relying on incidental serial execution.

`web/src/lib/pickup.ts` derives waiting text from visible working reports, passed due gates and
a recent agent roster. The card and task window share this helper, including after reload;
no polling is introduced. Roster refreshes occur at load, SSE reconnect and relevant events,
with generations preventing stale responses from overwriting new data or a signed-out scope.
The text deliberately does not assert queue receipt, order, capacity or an ETA. The item view
carries `run_requested_at` (`items.views`, `ItemOut`), and `store.pickup()` falls back to it
when the page did not see the press, so an explicit Run now still says "Waiting for pickup…"
after a reload. New overdue cards receive their grace interval from creation, not the
already-passed due time.

Concurrency regression tests exercise real isolated HTTP/SSE and child processes for four
scheduled cards, one/four slots, offline startup, and exactly-once execution. A runner
copied out of the repo must be updated along with it: check that all workers are idle before
replacing a running copy.

On Windows, `BoardFiles` normalizes path separators before immutable asset-cache matching.
