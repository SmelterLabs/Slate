# Agents: reports, the work loop, Run now, Asks and the card conversation

How an agent takes work from Slate and talks back. The agent-facing version (which calls to
make) is `docs/agents/`; this is the server side. Related: `docs/agent-kit.md` (onboarding,
clients, the skill), `docs/chat.md` (chat), `docs/notifications.md` (push).

## Agent reports, presence, roster and rate limits

- **Reports** (`service/reports.py`, `POST /items/{id}/report`, MCP `slate_item report`): an agent
  says `working | blocked | review | failed | done | idle` with an optional `note` and
  `progress`. The current flag lives in `items.agent_state/_by/_at`, `agent_note`,
  `agent_progress` and every report is one `item.reported` event (`after = {state, note,
  progress, by}`), so history has every report. It is a flag beside the status, never a status
  (rule 4). `done` clears the flag and, with `complete` (default), moves the item to the list's
  first done status; `idle` clears it only. `blocked` + `waiting_on` sets
  `waiting_on_account_id`; `assign_me` adds the reporter. **Asking a person**
  (`reports.report` owns the rule for finish, direct reports and closing lines). A leased run
  finishes with `ask`. Its stored badge is `blocked`. An interactive agent's direct report
  uses that badge and supplies one to four `questions` or one `question` with optional
  `options`. Each question has one line of 1 to 200 characters. Options contain 2 to 4
  answers of 1 to 40 characters each. With no `waiting_on`, the ask waits on whoever asked
  this turn, else the workspace owner (`brief.default_ask`). It posts one comment (the
  `reply`, else the headline) with `comments.ask_json = {questions: [{question, options}]}`.
  The headline is the single question or “<n> questions: <first question>”. The badge uses
  that headline, and the standing line caps it at 140 characters. `item.reported.after`
  and both ask views carry `questions`, `question` (the headline) and `options` (the choices
  for a single question, empty for several). The item view adds `comment_id`, `by_id` and `at`
  while the card waits on that person through an open ask.
  `blocked` waiting on an agent, or on nobody, needs no question. An agent's `PATCH` (or bulk
  update) of `waiting_on_account_id` to a person is refused the same way
  (`reports.refuse_person_wait`, at the route); a person's is not. `failed` is the badge of a
  run that broke (below, "Failed runs"): nobody is told.
- **Standing** (one line of truth on every card): report and finish take `status`, one line of
  plain text, 1 to 140 characters (checked at the API: `schemas.STATUS_FIELD`). Without it
  `reports.report` derives it, the one place that does: the first non-empty line of `reply`,
  else of `note`, Markdown marks and link targets taken out, cut to 140
  (`reports.standing_line`), else the state's label. An ask's question is its status (a
  `status` with an ask is refused). Only an agent's report other than `working` sets it
  (`items.standing_line`, `standing_by`, `standing_at`); it is part of the `item.reported` and
  `item.work_finished` events that caused it (`after.status`; a finish carries it only when
  it wrote it, so a person's finish never repeats an agent's line) and writes no event of its
  own. `status` is trimmed at the API and refused when empty or more than one line.
  The item view's `standing` is `{line, by_id, at, move_id}` (null until an agent records a standing line) and
  stays after the card closes. `move_id`, whose move it is, is computed in `items._move_id`:
  who the card waits on; else, for a `review` flag, the person the review is for
  (`items.review_for`); else nobody on a done or cancelled card; else its first agent
  assignee; else its first person assignee (assignees by handle); else null. Only accounts
  the viewer may see count: a guest gets no `standing` written by a workspace agent, and a
  move that would name one goes to the next case.
- **Review needs something to check**: `review` without `status` is refused (`REVIEW_ADVICE`:
  finish `done`, or say what to check). Its optional `waiting_on` is the person who checks it
  (default: whoever asked this turn, else the owner); it does not set who the card waits on.
  A bare closing line `SLATE: review` is `done`. A note is also posted as a comment (`comment=false` to skip); the board's feed
  folds that echo away (`agents.svelte.ts` `isReportEcho`). A status change settles the flag in
  the same change (`lists._settle_agent_flag` -> `reports.clear_flag`, recorded as an `idle`
  report by the mover): a person's move clears a `review` flag, and a move to a done or cancelled
  status clears a `review`, `blocked` or `failed` flag whoever makes it, an agent included. A `working` flag
  is never cleared by a status change, and an agent's own move that does not close the card leaves
  the flag to its next report.
- **Presence** (`slate/presence.py`): in memory only. `auth.require()` marks `api`, the MCP local
  backend marks `mcp`, the SSE stream counts open streams per account. After a restart it falls back
  to token `last_used_at`. Never persisted: it is bookkeeping, not a change, so it has no event and
  `/agents` is not in `CACHEABLE`.
- **Roster** (`service/agents.py`, `GET /agents?kind=agent|person|all`, MCP `slate_agents`): one row
  per account with presence, what it is on (items whose flag it set), open assignments, events in
  the last 24 h (index `ix_events_actor_id_id`) and how often the rate limit refused it. "What it
  is on" lists only flagged items that are not archived, not trashed and not in a done or
  cancelled status, the same rule as Needs you (`web/src/lib/attention.ts`).
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
- **Check-in** (`service/checkin.py`, `GET /checkin`, optional MCP `slate_checkin`): the agent's poll,
  separate from the people-facing Inbox below. `queue` (open items assigned to or waiting on the
  caller) plus `news` (others' events that concern it, with `why`) from one cursor. SQL first keeps
  only candidate events (assignment, mention and waiting-on events, and anything on an item the
  caller holds or watches), then `checkin.why` judges each in Python against the caller's *current*
  assignments and watches, in pages of 500 (at most 10,000 candidates a call, then `has_more`). The
  call snapshots the newest event id first; when nothing more up to it concerns the caller,
  `next_since` jumps to it, so a cursor never rereads and other people's busy boards cost an agent
  nothing. `item.created` is never news (its `item.assigned` follows). In `CACHEABLE`: both halves
  are event-backed.
- **Interactive claim** (`report(..., claim=True)`, REST or MCP `slate_item report` with
  `state: working` and `claim: true`): an atomic
  take. Inside `apply_change()` (so under the write lock) it refreshes the item and raises
  `Conflict` (409) when another account's flag is younger than `reports.CLAIM_FRESH` (6 h, the age
  the board dims a badge); it implies `assign_me`. Two agents racing for one item cannot both win.
- **Check-in queue cap:** `queue_limit` (default 50, 0 = news only) and `fields` (as on `/items`);
  `queue_total` says how many there are. `checkin.queue()` sorts on `(id, priority, due_at)` rows
  and loads only the returned items, and the route serialises with `api.checkin.dump()` (trims the
  queue items) straight to a `Response`. Without the cap an agent holding 400 items would get
  about 333 KB per check-in.
- **Hand to an agent**: in one atomic `POST /batch`, call `item update` with the new assignee set
  and `comment add` with `@agent <instructions>`. The agent sees it
  through its inbox or stream.
- **Board:** `AgentBadge.svelte` (card and task window), `AgentsPage.svelte` at `#/agents` (a page,
  not a scope: `parseRoute` returns `page: 'agents'` and the selected scope is left alone), store
  `lib/agents.svelte.ts` fed by the board store's `#apply`. The badge dims a `working` flag older
  than six hours. The badge offers **Mark reviewed** on a `review` flag (also in Needs you) and
  **✕ Clear** on a `blocked` flag; both call `task.clearAgentFlag`, an `idle` report by the viewer
  with `comment: false`. Clear takes off the badge only and leaves the card's Waiting on field as
  it is; a blocked badge that waits on nobody also shows Run now.

## Agent work loop and agent stream

A task returned by `POST /work/next` or `slate_work next` includes the text
`brief` also available from `GET /items/{id}/brief`. The task's list note appears
at the start of the rules under “This list:” when set. Check-in lists carry
`note_md`, `kind` and `reference` alongside their identifiers and names, so an
agent can identify a work list and read its note before filing. Filing without
a list or parent uses the configured inbox list when the caller can edit it
and it is an active work list. Reference cards stay available through search and
direct reads. Work offers, queue counts and the daily digest omit them.

- **Work loop** (`service/work.py`, `api/work.py`, MCP `slate_work`): `POST /work/next` takes the
  best free task for the caller, `POST /work/{id}/heartbeat` keeps it, `POST /work/{id}/finish`
  reports back, `GET /work` lists what the caller holds, each with its `state` (`working` or
  `expired`, from `work.lease_state`; `docs/state-machines.md`). A take is an ordinary claim
  (`reports.report(state="working", claim=True, comment=False)`), so the board needs nothing extra:
  `item.reported` + `item.assigned` events. On top of it sits a **lease** in `work_leases`
  (one row per held item: account, `until`, `run_id`). The lease is
  bookkeeping (`core.BOOKKEEPING_MODELS`, like token `last_used_at`): a bare heartbeat moves `until`
  with no event, so an agent beating every minute does not flood the history; a heartbeat with
  `note`/`progress` is a real `working` report, unless it repeats the note and progress the badge
  already shows (then it is bookkeeping too: a runner that re-sends the same note every few seconds
  would otherwise bury the card's history under identical rows).
- **Which task:** `work.eligible()` is one SQL select (outer join on the lease). Scope = assigned to
  or waiting on the caller, plus (with `pool`) items with no assignee; restricted callers only get
  lists they may edit (`access.EDIT`). Skipped: non-task items, archived items and items in
  archived lists or sections, Parked cards, reference-list cards, and cards in
  `links.blocked_item_ids()` (the same "open blocker" rule as the item view; the dependency thread
  keeps it the source of truth), `start_at` in the future unless Run now is pending,
  `waiting_on` someone else, `review`
  badge, the caller's own `blocked` badge younger than `BLOCKED_PAUSE` (1 h, so a blocked task is
  not retried in a loop), the caller's own last finish `answered` (until a new Ask or Run now),
  a `failed` badge until its retry time (the scheduler's retry and Run
  now both set `run_requested_at`, which is the override), and held by another account (a live
  lease; with no lease, a badge younger than `reports.CLAIM_FRESH`, except that a parked
  `blocked`/`review` badge never holds a card against an open Ask for the caller and a `failed`
  badge holds nothing; `reports._check_claim` applies the same rule at the take).
  An open Ask for the caller overrides the `review` badge and `waiting_on` someone else, as Run
  now does, and is the only way a done or cancelled card is eligible. A new Ask after a failed
  finish can wake that agent before its scheduled retry.
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
  A finish posts no note or reply when the agent has already reported the same state with a
  comment of its own during this turn (`work._already_answered`; the turn starts at its report
  moving into `working`), so a tool-side report followed by the runner's finish leaves one answer.
  An interim comment without a matching report does not suppress the finish's comment.
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
- **A card closed during a run.** Heartbeat, finish and a proved report answer 409 ("work was
  closed, archived or unassigned; stop execution") once the card is done, cancelled, archived or
  no longer the holder's, so the runner stops the command. The holder's own close is not that
  stop (`work._closed_it`: the card's latest `item.status_changed` is by the lease's account):
  its run keeps its lease and its finish posts the answer and gives the lease up; a `review`,
  `ask` or `blocked` outcome then leaves no flag and no waiting-on, because a closed card needs
  nobody (an ask's question is posted as a plain comment).
  A lease that runs out on a closed card (done or cancelled; not an Ask turn taken on a card
  that was done already) is settled by the scheduler instead of quarantined
  (`work._settle_closed`): `item.work_finished` by `system` (reconciled, expired,
  `runtime_state` `unknown`), the holder's flag cleared, the lease dropped. An archived or
  unassigned card can be run again, so its lease is still quarantined.
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
  skipped except reminders, digests, its own `item.run_requested` (Run now from another session
  of the same agent) and its own reconcile of an expired run (`item.work_finished` with
  `reconciled` and `expired`), which wake its runner. Counts as a live stream on the Agents page.
- **MCP work models:** `slate_work next` accepts the original run proof for resume.
  `slate_work finish` uses `reply` for a comment and `note` for a machine failure or hand-off.
  Take more work in a separate `next` call. The resident runner uses the REST models.

## Run now, the due gate and the runner

Slate stays the doorbell: it never launches an agent.

- **`item.run_requested`** (`work.request_run`, `POST /items/{id}/run` 202 → the event, MCP
  `slate_item run`): actor = who pressed, `after = {for: [agent account ids], reason: "now"}`, field
  `run_requested_at`. 422 with no agent assignee, or done/cancelled/archived. In the same
  transaction, it clears `parked_until` and `parked_note` through ordinary field changes. Its
  pending request overrides `start_at` for pickup and leaves the saved date in place.
  `work.take()` clears `items.run_requested_at`.
- **Due gate** (`work._run_time_has_come`, inside `eligible()`): when the caller is an agent, a task
  assigned to it with a `due_at` is takeable only once its run time has come or `run_requested_at`
  is set. Run time = `dates.run_at(due_at, due_date_only)`: 06:00 in the workspace timezone on the
  day for a date-only due date (`items.due_date_only`), else `due_at` (a task explicitly due at
  17:00 runs at 17:00). SQL cannot do timezones, so the condition is `due_at <= now OR
  (due_date_only AND due_at = dates.early_date_only_due(now))` (the one 17:00 instant whose 06:00
  has passed, between 06:00 and 17:00 local). The due gate also admits an open question addressed to the agent. It does not hold
  undated cards, cards taken by people, or cards the agent does not own.
- **`later` finish** (`work.finish`, `outcome: "later"`, closing line `SLATE: later`): the agent
  did what it can now and the rest must happen at the due time, with any go it needs already
  given. It reports `idle` (badge cleared), keeps its assignment, sets no `waiting_on` and
  completes nothing; the due gate then holds the task until its run time, and the run stage
  wakes the agent again. `work.check_later` refuses it (422) unless the task's run time is still
  to come, because otherwise nothing would run it again (or the gate would hand it straight
  back); `waiting_on` and `hand_to` are refused with it. The resident runner sends `later` on a
  card with no due time still to come as `answered`, so the answer is kept and nobody is asked
  anything. This is the honest ending for "scheduled, nothing needed from you".
- **`ask` finish** (`outcome: "ask"`, `questions` or one `question` with optional `options`,
  and optional `waiting_on`): use it for every question a person must answer or decide, even
  after the rest of the work is finished. Review is for inspecting finished work and carries
  no questions. `work.finish` maps ask to a `blocked` report (above, "Asking a person").
  In a reply ending, put one trailing `SLATE: ask alex: <question> [a | b]` line per question,
  up to four. Slate reads back from the end, allowing blank lines, and stops at a different
  handle or other text. The ask lines live in the question block, and everything before the
  run stays in the reply body. Other endings keep their closing line in the comment. A malformed
  agent ask returns 422 with the validation reason and posts no comment. A valid ending that
  cannot apply leaves the comment body as written. Slate validates any explicit `assignee`
  and checks that the caller can reach it before applying the ending. An applied closing Ask
  assigns the comment once, to the person asked in the ending. An agent only
  asked something on a card may ask whoever asked it (the third-person rule below).
- **Answering an ask** (`comments.answer`, `POST /comments/{id}/answer {option}`, MCP
  `slate_comment answer`): the person asked (or an admin) picks one of the options. It posts a
  comment by them whose text is the option, addressed to the agent (the reply path below for the
  person waited on; an explicit Ask to the agent for an admin), and `comments._answered` makes
  the card stop waiting on the person and resolves their open asks on it (`comment.resolved`,
  `reason: "answered"`), so the item's `ask` is null. A typed reply by the person does the same
  through `add_comment`'s reply branch. For several questions, answer them in one comment.
  The option route returns 422 for several questions or an unavailable option, 409 once answered,
  403 for anyone else.
- **`answered` finish** (`outcome: "answered"`, `reply` required, closing line `SLATE:
  answered`): the agent replied and nothing else changes. Its reply is posted, its own badge is
  cleared (an `idle` report), status, assignees and `waiting_on` are untouched, the Asks this
  turn took (`turn_asks`) are resolved with `reason: "answered"` (as on every agent finish but
  `failed` and `release`), and the lease ends. The card
  is then not offered to that agent again (`eligible()`: its newest `item.work_finished` on the
  card says `answered`, `work._last_outcome`) until someone asks it something there or presses
  Run now, so an open card it only answered on does not loop.
- **Failed runs** (`outcome: "failed"`, `note` required): the machine broke and no person is
  asked anything. The note (up to 4000 characters) is stored on `item.work_finished` (`after.note`,
  and `after.failures` = the agent's consecutive failed finishes on the card, counted by
  `work._failures` back to its last other finish or the last Run now a person pressed) and is
  never posted. The badge is `failed` with the note's first line; `waiting_on` is untouched; the
  lease ends; nobody gets an inbox entry or a pop-up (`inbox.reason_for` drops `item.reported`
  into or out of `failed`). Retry (`work.RETRY_AFTER`, 5 then 30 minutes after the failed finish):
  the scheduler's due tick runs `due.retry_failed_runs`, which records `item.run_requested` by
  `slate` (`after = {for: [the agent], reason: "retry"}`) and sets `run_requested_at`, once per
  failure, so the work loop and the agent stream hand the card out as for Run now. The third
  consecutive failure (`work.GIVE_UP_AFTER`) records `item.run_failed` (`after = {agent_id,
  failures, note, notify}`) in the same change and nothing retries it; `notify` is the card's
  person assignees and watchers, else the workspace owners, told as inbox and push reason
  `failed` ("<Agent> could not run: <title>"). Run now starts the count over.
- **Stale badges** (`due.clear_stale_badges`, on the due tick): a `working` badge with no lease
  and no report for `work.STALE_AFTER` (30 minutes) is cleared, an `idle` report by `slate`
  with `after.reason: "stale"`, after the same test passes again under the write lock (a take
  or report that won the gap is left alone). It reaches nobody (`inbox.reason_for`).
- **Stalled cards** (`due.poke_stalled`, on the due tick): a card can receive a retry when it is open,
  on a work list, unparked, assigned to an agent, waiting on nobody, and has no `review`,
  `blocked` or `failed` badge. It has no open blocker, future start or due time, or lease. An enabled
  agent assignee that may edit it last finished it `answered` at least `work.STALL_AFTER`
  (4 hours) ago with no Run now since. Every condition is read again under the write lock
  before anything is written. Slate asks that agent to try again once: `item.run_requested` by
  `slate` with `after.reason: "stalled"` and `for` = that agent. The poke is that agent's
  alone: `eligible()` opens the card for it once (`work._stall_poke`: a stalled poke naming it,
  newer than its newest finish there), and its next take on the open card binds the poke to the
  turn (`item.work_started` `after.poke`). Another assignee's take is an ordinary turn and leaves
  the poke waiting. The brief for the bound turn ends with `brief.STALLED_RULE` (the card is
  still open; end `done`, `ask`, `later` or `review`; `GET /items/{id}/brief` reads the
  caller's own turn). The brief also tells a retried run what broke: when the caller's newest
  finish on the card was `failed`, it ends with `brief.FAILED_RULE` and that finish's note,
  quoted as information (`work.last_failure`), in `GET /items/{id}/brief` and in the brief of
  `POST /work/next`. If that turn ends `answered` too, on a card still open, unparked,
  on a work list and without a future start or due date, the finish sets
  `items.stalled_at` (the item view's `stalled: true`) and records `item.stalled` (`after =
  {agent_id}`), the inbox and push reason `stalled` ("<Agent> stopped without finishing:
  <title>"), told to the card's enabled person assignees and watchers who can see it now, else
  the owners (`inbox.stall_recipients`). No further poke. Run now (`request_run`), a new Ask
  for an agent (`comments._assign`), parking, scheduling a future start or due date, closing
  the card, or any finish other than `answered` clears it; an
  `answered` turn the poke did not start leaves it as it is.
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

- **Model**: `comments.assignee_id` (indexed), `resolved_at`,
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
  assign the agent (`reports.report(no_assign=True)`, lease `asked_only`); `finish done`
  answers instead of completing (`complete=False`). Every agent finish but `failed` and
  `release` resolves its taken Asks (`reason: "answered"`). An agent finish other than
  `failed` consumes its take's dispatch boundary without changing visibility on its own.
  `/work/next` returns only the pending `asks` within that take; new/reassigned Asks wait for the
  next turn. An asked-only finish
  `ask` or `blocked` may wait only on the author of an Ask the take answered
  (`work._waits_on_asker`); naming anyone else turns it into an `idle` report with no
  `waiting_on`, so the answer (an ask's question included) is a comment and the card gets no
  badge and no wait.
- **Board**: Composer "Ask…" select (people and agents) turns Send into "Ask <name>";
  `ActivityPanel` shows "Asked X · Open/Resolved" (`data-ask`) for all Asks, plus Resolve for the
  assignee or author. Typing uses the work-finish receipt, not merely unresolved state. Reducer
  refetches on Ask/work events. The picker lists every pickable account except the viewer and
  starts on `conversation.defaultAsk`: the card's agent assignee, then the card's project agents
  (`board.projectAgents`, guest-role agents first), the agent last asked on the card, the agent
  whose flag is on it, the viewer's last Ask on this device (`localStorage slate.lastAsk`), then
  the oldest push agent; with no agent it starts on a plain comment. Until the viewer picks
  someone, the Composer addresses the Ask from the draft (`conversation.askFor`): the first agent
  `@mentioned` (`mentionedAgent`, never `slate`), else no Ask when it `@mentions` only people,
  else the picker's default. The server addresses the Ask to exactly the `assignee` it receives.
  E2E: `web/e2e/ask.spec.ts` and `conversation.spec.ts`.

### Ask dispatch and settlement (protocol 3)

`item.work_started` records the take's ordered event boundary. On a non-failed finish, the
server records the settlement cursor in `item.work_finished.after.through`, advancing it as
described below.
Every agent finish except `failed` and `release` resolves questions in that boundary with
`reason: "answered"`. Those two outcomes leave the questions open. A failed finish leaves the
original Asks and any messages seen mid-run pending for the next take. Human finishes also leave
questions open. An agent taking an asked-only turn can finish `done` without completing the
card. Questions outside the settlement boundary remain pending. The board uses the finish
receipt to stop typing indicators and comment events to update question state.

### While a run is going: new messages and Stop

An Ask addressed to an agent that already holds the card is not a new turn yet. The run's
heartbeat answers with it (`messages`, the comments addressed to the agent since its take that it
has not marked seen: `work.turn_messages`), the runner hands it to the running agent and sends
its id back (`heartbeat {seen: [...]}`), and that records `comment.seen` (once per comment;
ids that are not this run's messages are ignored). The comment view then carries `seen_at` /
`seen_by_id`, which the board shows as "<agent> got this mid-run". A non-failed finish settles
what was read:
`item.work_finished.after.through` is `work._turn_through`, the take's boundary moved past each
seen message in order and stopped at the first one never read. Unread messages beyond that
boundary can start a later turn.

`POST /items/{id}/stop` (MCP `slate_item stop`) records `item.stop_requested` with `after.for` =
the account holding the lease; 409 when no live lease exists, and a second press in the same run
returns the first event. Slate does not take the lease away: the heartbeat answer carries `stop`
(`{by, by_id, at}`), the runner ends its command, and the agent's own finish (`answered`, saying
where it stopped) ends the run. A Stop also settles every message sent before it, so an
unread message does not start the run again; what the person writes after Stop is the next
turn. Both the stop and the seen marks are derived from events after the take's
`item.work_started`, so a new run starts clean with no state to reset.

The agent stream sends a third message kind, `control` (`{id, item_id, type}`), to the agent a
`comment.assigned` or `item.stop_requested` concerns when it holds a live lease on that item
(`work.is_running`), so a runner heartbeats at once instead of waiting for its timer (a third of the
lease). The stream is only the doorbell; the heartbeat answer is the source. Tests:
`tests/test_mid_run.py`, `tests/test_mid_run_stream.py`, `tests/test_runner_control.py`.

## Wake modes, Copy brief, the `SLATE:` line and the adapters

The agent-facing version is `docs/agents/pull-agents.md`.

- **`accounts.wake`** (`push` | `pull` | `manual`, default `push`): set on
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
  `add_comment(closing_line=True)`, which REST and MCP (`slate_comment add`)
  pass. Slate's own comments (report notes) never do, so a note can't re-trigger. An agent author is
  reported through `reports.report(state, comment=False, complete=<assigned>)` with the
  comment as its reply (so the standing is derived from it) and the text after a second colon
  (`SLATE: done: Sent the invoice.`) as its `status`; a bare `SLATE: review` is `done`;
  `answered` clears its own badge only; `ask <handle>: <question> [a | b]` is a `blocked` report with the
  question, and the comment itself becomes the ask (addressed to the person, `ask_json` set). A
  person gets `done` → the list's done status (`reports.done_status`); the rest change nothing.
  `Invalid`/`Conflict`/`NotFound` are swallowed after the comment commits. A comment with no
  line changes nothing (the runner, not Slate, treats an answer with no line as `answered`).
- **Adapters** (`integrations/`, rule 5): `hermes/run_task.py` for Hermes Agent (one gateway
  `api_server` session per card, see below) and
  `runner-example/run_task.py` (a worked example for an agent service reached over HTTP: one
  idempotent `POST /runs` per take, then `GET /runs/{id}` until it ends). Each folder's README
  holds the facts and the unverified points. Tests next to them (loaded by file path) run in CI
  as `uv run pytest -q integrations`. Nothing in `src/` or `tests/` imports them.

## The card is the conversation

Slate holds only generic pieces; everything runtime-specific is in `integrations/`.

- **Thread reference** (`models.AgentThread`, table `agent_threads`): one
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
  scheme, and every scheme still needs a host part (with `myapp` listed,
  `myapp://chat/<id>?profile=my-agent` passes and `myapp:chat` does not). `GET /me` carries `link_schemes`, and the board
  (`conversation.safeAgentUrl`, used by `AgentBadge`) renders an Open link only for a scheme the
  server named (http(s) when it named none), so a desktop app's link is followed only where Slate
  was configured for it; a non-http(s) link opens in the same tab (the OS hands it to the app)
  instead of `_blank`. `javascript`, `data`, `vbscript`, `blob` and `file` are never
  accepted (`settings.UNSAFE_LINK_SCHEMES`): the setting refuses them at start-up, and
  `check_url` and `/me` drop them even so. Slate knows nothing about any agent runtime: the
  scheme is configuration on the server, and the adapter decides what link it sends. The
  bundled Hermes adapter (`integrations/hermes/run_task.py` `thread_ref`) is what sends
  `hermes://chat/<session id>?profile=<profile>` (profile from `HERMES_PROFILE`, else the
  `/p/<profile>` tail of `HERMES_API_URL`), which Hermes Desktop's `hermes://chat` deep link opens
  on the owning profile. `HERMES_SESSION_LINK=0` keeps the ref without the url for a Slate that
  lacks the scheme (a refused url is a 422 on the whole report).
- **Reply** (`report`/`finish` `reply`, ≤ 20,000): one plain comment by the agent instead of the
  labelled `**Blocked** · note`; the badge note is `note` or the reply's first line.
- **Reply turns** (`comments.reply_target`): a comment by the person an agent's `blocked` badge
  waits on (`waiting_on_account_id` = the author, `agent_state_by` an enabled agent, the author not
  an agent) is a reply when it is plain or explicitly addressed to that same agent; a comment
  addressed to anyone else is not a reply. A reply is addressed to that agent as an Ask
  (`comment.assigned` with
  `after.reply = true`) and, in the same change, clears `waiting_on` and resolves the agent's
  open asks of that person on the card (`comments._answered`). The Ask does the rest: it is a
  `WAKE_TYPES` hint (the agent stream adds `comment_id` and `reply` to the hint), puts the task in
  the agent's scope, ends the blocked pause (`eligible()`: an open Ask for you skips
  `BLOCKED_PAUSE`), and `/work/next` returns the newest person-written open Ask as `reply`. Replies
  leave it open until explicitly resolved.
- **Mid-turn reporting (protocol 3):** a resident runner owns heartbeat/finish. Its child must not
  independently report review/done or start another work loop. Reports during a protected attempt
  need the matching proof; a missing lease is never recreated from a badge. Definitive control loss
  stops local execution and preserves unknown remote state for reconciliation.
- **Run now after the last report** (`eligible()` `pressed`: `run_requested_at > agent_state_at`)
  overrides `waiting_on` someone else, the `review` and `failed` badges and the blocked pause, and
  starts a failed run's retry count over. **Run now while
  working** is 409 (`work._refuse_if_working`: a live lease, or a fresh working badge without one)
  with "My Agent already has it (working 4 min)". `take()` keeps `claimed_at` while the same
  account holds the lease, so that age is the run's, not the last heartbeat's.
- **Waiting on you**: the `waiting_on_account_id` change is inbox reason `waiting`. An ask
  that waits on the same person again (no field change) carries `after.waiting_on` and
  `after.again = true` and is the `waiting` entry instead (`inbox.reason_for`; the inbox
  pre-filter uses `json_extract(after_json, '$.waiting_on')`). The ask's own `comment.assigned`
  (`after.ask`) is not also a `mention`. The push's headline is the question, its body "<Agent>
  asks on: <title>", when the card has an open ask (`comments.asks_of`).
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
- **Live activity** (`slate/liveactivity.py`): what a run is doing right now, for the box under
  "is typing…". `POST /work/{id}/activity {run_id, lines[]}` (MCP `slate_work activity`) needs
  the caller's live lease (`work.holds`, the same proof as a heartbeat) and writes nothing: no
  event, no comment, no row. The last `KEEP` (60) lines of the current run live in memory per
  item (a new `run_id` starts over; a restart forgets them), each cut to one 300-character line
  (`clean`). `GET /work/{id}/activity` returns them only while the item's flag is `working` by
  the account that posted them, and nothing of a workspace agent to a guest. Each post is also
  handed to open boards as an **id-less** `item.activity` message through `bus.publish_live`,
  which reaches only subscribers that asked (`bus.subscribe(live=True)`: `/events/stream`), so
  the engines, long-polls, `/checkin/stream`, `GET /events` and history never see it
  (`docs/live-stream.md`). It is deliberately not an event: a turn starts a tool every few
  seconds, and product rule 3 would make each one a history line. Posts count against the
  token's write bucket, so a runner batches (`tests/test_live_activity.py`).
  **Run log.** A run's lines outlive it when it leaves an answer: `work.finish` calls
  `_keep_run_log`, which stores every line the run posted (up to `liveactivity.LOG_KEEP`, 400;
  the live box shows the last 60) as a `comment_runs` row on the caller's newest comment since
  its take, covered by the `item.work_finished` event (`after.steps`, `after.comment_id`), and
  then clears the run's lines. Comment views carry `run_steps`; `GET /comments/{id}/run` (MCP
  `slate_comment run`) returns the lines. No lines, no comment, or a restart mid-run: no log.
  A guest sees neither the count nor the log of a workspace agent. The row goes with its
  comment and its item (cascade). Tests: `tests/test_run_log.py`, `web/e2e/run-log.spec.ts`.
  Board: `lib/liveactivity.svelte.ts` (`liveActivity.lines[itemId]`, `mergeLines`) is fed by
  `BoardStream.#receive`, which takes `item.activity` before the cursor check (no id, so no
  cursor, reducer or detail refetch); `ActivityPanel` loads the run's lines when the open task's
  agent is working and clears them when it stops; `task/LiveActivity.svelte` is the box
  (`data-live-activity`): fixed height, `overscroll-behavior: contain`, follows the newest line
  unless the reader scrolled back inside it. E2E: `live-activity.spec.ts`.
- **Adapters**: `slate_runner.py` forwards `reply` and `thread` (result and progress lines) and
  posts a progress line's `activity` (a string or a list) to the live box, giving up for good on
  a server that answers 404/405. The Hermes adapter turns its turn stream into those lines
  (`activity_line`: a tool with its preview, a failed tool, thinking, commentary, "Writing"),
  at most one request per `HERMES_ACTIVITY_EVERY` (1 s) with late lines flushed by a timer
  (`Activity`), for card turns only. Hermes
  uses one `api_server` session per card, a watchdog (`/health` start time, session 404, gateway
  silent for `HERMES_DOWN_GRACE`) and approvals as Asks; facts and unverified points in
  `integrations/hermes/README.md`. The runner example sends the answer on the card as the next turn
  and reports the agent's page as the thread.
- **No `SLATE:` line is never `review`**: `review` means the agent says the work is finished, so
  a reply without the line must not send a card to review while its agent is still waiting on
  background subagents. The gateway adapter asks again in the same session up to `HERMES_NUDGES`
  (default 2) times (wait for background work, then end with the line); still no line, or the
  CLI/runner-example adapters: `answered`, so the reply stands, nobody is asked anything and a
  person's next comment continues the same session.
  The brief's `RUN_RULE` also tells agents to wait for background work before answering.
- Tests: `tests/test_card_conversation.py`, `tests/test_clients.py` (runner reply/thread),
  `integrations/hermes/test_hermes_adapter.py`, `web/src/lib/conversation.test.ts`,
  `web/e2e/conversation.spec.ts`.

## Chat mentions wake an agent

A person's `@mention` of an agent in a channel or thread starts its runner, the way an Ask on a
card does; the reply lands in the chat.

- **Model** `models.ChatWake` (table `chat_wakes`): one row per (agent,
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
- **Shared turn code** (`service/turns.py`): chat and doc turns use one implementation for take,
  heartbeat, finish, expire, reconcile and clear. Each kind declares its wake model, event
  fields, mention lookup, access checks, runner context and reply posting.
- **A turn never strands later mentions.** An expired turn stays claimed (quarantined: the run
  may still be writing) and shows as `expired` in `agent_turns`. It is settled by the agent's
  `reconcile` with its run proof, by a person's Clear (`POST /work/chat/clear` or
  `/work/doc/clear` `{channel|doc_id, thread_id, agent_id}` for a person who may comment there),
  or by a person's new mention there (`turns.fold`): each records `agent_turn` `state: released`,
  `reconciled`, the person as actor, and a mention that waited is pending again (the next take
  answers it, and the new mention's `for` names the agent so its runner hears the hint). The old
  `run_id` stays fenced out of the new turn. A live turn is never cleared or folded over.
- **A turn the agent can no longer answer is dropped.** A pending turn whose place is gone or
  where the agent's own access fell below comment (a share lowered or removed) is ended by the
  take that meets it, and by the scheduler's sweep (`turns.expire`): `agent_turn` `state:
  dropped`, `reason` `gone|no_access`, so `/work/next` never fails on it and `remaining` never
  counts it for long. A turn only the calling token cannot reach (a list-limited token) is skipped
  and left for another token.
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

- **Model** `models.DocWake` (table `doc_wakes`): one row per (agent,
  doc, thread; `thread_id` = the thread's first comment, `""` for a body mention). Where chat
  compares message ids, a doc mention has no row of its own for a body save, so the row counts
  instead: `mention_no` goes up per mention, a take sets `taken_no`, and `pending` = `mention_no >
  taken_no`. `comment_id`/`excerpt`/`by_id` are the newest mention's. `(DocWake, "until")` is
  bookkeeping; the row follows the doc's visibility (`read_criteria`) and needs `comment` on the doc
  (`_COMMENT_MODELS`, `check_object`'s doc branch).
- **Service** `service/turns.py`, called from `docs._record_mentions` (body) and
  `doc_threads._record_mentions` (comments, Asks, `assign_comment`) inside their change, right after
  `doc.mentioned`. Same wake rule as chat: enabled `push` agents that may comment on the doc (their
  own `access.compute`), and never when the poster is an agent. One `doc.mentioned_agent` event
  (`after.for`, `after.queued`, `thread_id`, `comment_id`, `excerpt`). Repliers named only by
  `reply_to` (thread participants) are not woken: only a mention or an Ask is.
- **Turn** `POST /work/next`: tasks, then chat, then doc (`kinds` default `task,chat,doc`);
  `{doc_id, thread_id}` takes exactly that turn. `doc` carries the page (`doc_id`, `title`, `kind`,
  `path`), `section_md` (the heading section around `@handle` for a body mention, around the root's
  `anchor_quote` for an anchored thread; at most 8000 chars, `turns.section_around`),
  `body_chars`, `comments` (the thread), `mention` (`excerpt`, `by_*`, the comment), `may` (`reply`;
  `edit` when the agent can edit a page) and `lease` with `run_id`. Events `doc.agent_turn` `state`
  started / done (`reply_id`) / released / expired (actor `slate`, `reconciliation_required`) /
  dropped (`reason`).
- **Answer** `POST /work/doc/finish {doc_id, thread_id, run_id, outcome, reply}` is the chat contract
  with `doc_id` for `channel`: `done`+`reply` posts the agent's comment in the thread, or a new
  discussion thread for a body mention; release posts nothing; identical retries return the receipt.
  `/work/doc/heartbeat` and `/work/doc/reconcile` likewise. As in chat, the agent's own comment does
  not end a fenced turn (chat's unfenced `_answered` path is unreachable under protocol 3, so docs
  have no counterpart). `/work/doc/clear`, expiry, folding and dropping are chat's (above). MCP
  `slate_work doc_heartbeat|doc_finish|doc_reconcile`.
- **Stream**: `doc.mentioned_agent` with the caller in `for`/`queued` is a `work` hint `{doc_id,
  thread_id, comment_id, queued}` (`work.DOC_WAKE_TYPE`). Scheduler hook `turns.expire("doc", ...)` (Health
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

`GET /agents/plan` (read; `api/agents.py`, `service/agents.plan`) answers
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
The text confirms the request only. The item view
carries `run_requested_at` (`items.views`, `ItemOut`), and `store.pickup()` falls back to it
when the page did not see the press, so an explicit Run now still says "Waiting for pickup…"
after a reload. New overdue cards receive their grace interval from creation, not the
already-passed due time.

`PICKUP_WAIT_MS` is two minutes. An Ask to an agent that no run has taken by then shows "<agent>
hasn't picked this up (asked N ago). Check agent status." in place of the typing dots
(`conversation.unpickedAsk`, `data-agent-pending="unpicked"`). A Run now press reads "Waiting for
pickup…" and then "Not picked up yet. Check agent status.", or "No live connection seen for
<agent>. Check agent status." when a roster from within the wait shows no live stream for every
relevant agent.

Concurrency regression tests exercise real isolated HTTP/SSE and child processes for four
scheduled cards, one/four slots, offline startup, and exactly-once execution. A runner
copied out of the repo must be updated along with it: check that all workers are idle before
replacing a running copy.

On Windows, `BoardFiles` normalizes path separators before immutable asset-cache matching.
