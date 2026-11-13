# Recurrence engine and scheduler

Recurring rules, the occurrence engine, the materializer, the ClickUp-style options and the
in-process scheduler every background hook runs on.

## Recurrence engine and scheduler

Modules: `src/slate/service/recurrences.py` (rules, occurrence engine, `materialize`),
`src/slate/scheduler.py` (the asyncio loop), `src/slate/api/recurrences.py` (REST, with its own
request/response models; mounted by `recurrences_api.install(app)` in `app.py`).

**A rule** is `rrule` (one RFC 5545 RRULE line, stored uppercase without the `RRULE:` prefix) +
`dtstart` + `timezone` (IANA, default the workspace timezone). Validation:
`dateutil.rrule.rrulestr` must parse it, it must have a `FREQ`, and FREQ must be DAILY, WEEKLY,
MONTHLY or YEARLY (sub-daily is refused: a task board does not need 300 hourly items). No
`DTSTART`/`EXDATE` lines inside `rrule`;
`dtstart` is its own field. `dtstart` takes `YYYY-MM-DD` (midnight in the rule's zone; default
today), naive ISO (rule's zone) or aware ISO. `UNTIL` may be `...Z` (UTC) or a bare date (through
the end of that local day). `tags_json` holds tag names (created on first spawn);
`assignee_ids_json` holds account ids (the API accepts handles).

**Occurrences and DST.** `occurrences(rule, start)` runs dateutil on *naive local wall time* from
`dtstart` in the rule's zone, then attaches the zone (`ZoneInfo`, `fold=0`) and converts to UTC. So
"every day" stays at the same local hour across DST; the UTC instant moves by an hour. A wall time
that does not exist (02:30 on spring-forward day) resolves to the pre-transition offset (03:30 CDT
in `America/Chicago`, say).
Each occurrence has `at` (the instant, stored as the item's `occurrence_at`) and `due_at`: 17:00
local on the occurrence's date, unless the rule carries a time (`BYHOUR` in the rrule, or a
`dtstart` that is not local midnight), in which case `due_at = at`. Templates: `{date}` -> `Wed Nov
11`, `{iso_date}` -> `2026-11-11`, both the occurrence's local date; any other text, braces
included, is literal.

**The cursor.** `recurrences.next_run_at` is the first occurrence not yet dealt with. It is set at
create, resume and re-shape (PATCH of `rrule`/`dtstart`/`timezone`) to the first occurrence that is
on or after today in the rule's zone, on or after `dtstart`, and after the latest instance already
spawned. So a rule never back-fills the past, and a pause skips what fell inside it.

**`materialize(session, now)`**, per active rule (not paused, not ended, cursor set), in its own
`apply_change()` block, re-read under the write lock: walk occurrences from the cursor while `at <=
now + lead_days`:

1. If an item with `(recurrence_id, occurrence_at)` already exists, step over it.
2. `spawn_always`: spawn. Otherwise look for the open instance (latest by `occurrence_at`; not
   archived, status kind not done/cancelled). None open: spawn.
3. Open and `skip_if_open`: if this occurrence's `due_at` has passed, record `recurrence.skipped`
   and step on; else stop here (decided on a later tick).
4. Open and `roll_forward`: if the open instance is overdue, move its `due_at` and `occurrence_at`
   to this occurrence (`item.updated` per field, plus `recurrence.rolled_forward` with
   before/after); else stop here.

Then the cursor moves to the first occurrence not dealt with (or `None` when COUNT/UNTIL is
exhausted). Waiting rather than skipping is deliberate: with a 14-day lead a weekly rule always has
the next occurrence in the window while this week's is open; deciding at window entry would skip or
roll every week even when the person is on time. Completing an instance never touches the rule; the next
tick simply finds nothing open.

**Spawning** reuses `items._new_item()`/`items._insert()` (so position, default status, tags,
assignees and the first body version behave exactly like `POST /items`) and sets
`recurrence_id`/`occurrence_at` before insert. Events: `item.created` (+ `item.assigned`,
`item.updated` on tags, `tag.created` as needed) and `recurrence.spawned` (`item_id` = the new item,
`subject_id` = the rule, after = occurrence and due). The scheduler acts as the rule's `created_by`
with source `system` and `session_ref` `recurrence:<rule id>`; `run_now` acts as its caller. Rule
event types:
`recurrence.created|updated|paused|resumed|ended|spawned|skipped|rolled_forward|advanced`
(`advanced` only when the cursor moves with no other event, e.g. stepping over an existing
instance).

**Exactly one instance per occurrence:** the `items (recurrence_id, occurrence_at)` unique
constraint, the lookup in step 1, and the monotone cursor. A restart or double run finds
the cursor already moved; a stale read loses at the database. Tests:
`test_materialize_twice_is_idempotent`, `test_database_refuses_a_second_instance_of_one_occurrence`.

**Failure isolation:** a rule that raises is logged and skipped; the others still run and it is
retried next tick.

**Bounds:** `_clean_rrule` refuses `INTERVAL < 1` (dateutil yields the same instant
for ever, which would loop the tick inside the write lock), `BYMINUTE`/`BYSECOND`, and more than one
`BYHOUR` (sub-daily through the back door: 1,440 items a day in one transaction). `occurrences()`
stops when a rule stops advancing. `_dtstart` refuses years before `MIN_DTSTART_YEAR` (1970;
dateutil walks from `dtstart`, so an ancient start costs seconds per view) and turns `OverflowError`
into a 422. A rule spawns at most `MAX_SPAWN_PER_TICK` (100) per tick; the cursor stays on the next
occurrence and the rest follow next tick.

**Scheduler** (`slate/scheduler.py`): `start()` creates one asyncio task (first tick immediately,
then every `settings.scheduler_interval_seconds`, default 60, env
`SLATE_SCHEDULER_INTERVAL_SECONDS`); `stop()` cancels it. `lifespan` starts it only when
`settings.scheduler_enabled` (tests set `SLATE_SCHEDULER_ENABLED=false`). Each tick runs in a worker
thread (`asyncio.to_thread`) and calls every function in `scheduler.hooks` with `(session, now)`,
each with its own fresh `db.SessionLocal()` closed afterwards, so no transaction spans ticks. A
raising hook is logged; the rest run. `scheduler.last_tick_at` is the `now` of the last finished
tick (for `/healthz`).

**Hooks** (`scheduler.hooks`, in order): `recurrences.materialize`, `notify.due_events_tick`,
`notify.reminders_tick`, `notify.digest_tick`, `trash.purge_expired`, `work.expire_leases`,
`chatwake.expire`, `docwake.expire`, `sessions.notify_expiring`; `scheduler.POLLERS` names each on
the Health page.
**Adding one:** write `fn(session, now)` (idempotent, writes through `apply_change()`) and add it to
the `hooks` list in `scheduler.py`. Do not edit `_loop` or `tick`. Work that needs a faster cadence
than the tick (like webhook delivery) is its own task in `lifespan` instead.

**Preview and adopt.** `GET /recurrences/preview?rrule=&dtstart=&timezone=&count=` (read
scope; `count` 1-50, default 5) runs `recurrences.preview()`: the same
`_clean_rrule`/`_zone`/`_dtstart` validation as create (a bad rule is a 422 with a plain-English
`detail`), a transient unsaved `Recurrence`, `next_occurrences()`, and `rrule_text.describe()`. It
returns `{rrule, description, timezone, dtstart, occurrences}`; rule views carry `description`
too. `adopt_instance(rule, item)` (REST `POST /recurrences/{id}/adopt {item_id}`, MCP `adopt`, and
`adopt_item_id` on create, all in one change) makes an existing item the rule's current occurrence:
it sets `recurrence_id` and `occurrence_at` (the rule's occurrence on the item's due day, else the
due instant itself; an undated item takes the next occurrence and its due date), then re-anchors the
cursor past it (`recurrence.advanced`). With `adopt_item_id` and no `dtstart`, the rule anchors on
the item's due day. This is how the board's "Make recurring" keeps the open item as this occurrence.

**ClickUp-style options.** Four columns on `recurrences`:
`trigger` (`schedule` | `on_done`), `skip_weekends`, `reopen` (only with `on_done`, else 422),
`copy_checklists` (default true). `skip_weekends` lives inside `occurrences()`: a Sat/Sun wall time
moves to the Monday after and a day already emitted is dropped, so the uniqueness rules and the
cursor see shifted instants (iteration starts three days early). `on_done`: `materialize` only
spawns the first instance (then `next_run_at = None`; `_anchor` returns None once any instance
exists); `lists.move_item_to_status` calls `recurrences.on_item_done(cx, item)` in the same change
when an item enters a `done` status. It acts only for the rule's newest instance, re-anchors the
rule on the local day it was done (COUNT dropped, UNTIL kept; `next_after_done`) and never dates the
next one on or before the finished one's occurrence. With `reopen` the same item gets the next dates
(start keeps its distance), the list's default open status, unticked checklist entries and
`recurrence.reopened`; otherwise `_spawn` runs with the done item as checklist source. A rule with
no date left ends (`recurrence.ended`). `copy_checklists` copies the previous instance's checklists
into each spawned item. `on_done` views have empty `next_occurrences`; `preview(trigger=on_done)`
shows the chain "if each is done on its due day, from today". `GET
/recurrences/upcoming?start&end&list_id` (≤ 400 days, ≤ 1000 rows; MCP `upcoming`) lists dates
active `schedule` rules will fill from their cursor on, for an agent or client that draws future
repeats on a calendar; the board does not call it. The board loads every rule at start
(`board.loadRules`) for the card label (`lib/rrule.ts` `shortRule`).

**REST** (`/api/v1/recurrences`): `GET` (filters `list_id`, `include_ended`), `POST` (201),
`PATCH /{id}` (any field; `ended: true` ends the rule for good, `false` is 422), `POST
/{id}/pause|resume|run-now` (the REST path is `run-now` only; the MCP action is named `run_now`).
Writes need `write`. Every response carries `next_occurrences`: the next
5 whose due time is still ahead, each `{local, utc, due_at}` (empty once ended). `run_now` returns
`{recurrence, spawned: [items]}` and is 422 on a paused or ended rule. `GET /{id}` exists
because the MCP `get` action's stdio binding calls it. `GET
/preview` and `POST /{id}/adopt` are described above; `/preview` is declared before `/{rule_id}` so
the path is not read as an id.

**Testing recurrence:** service functions take `now=` so tests never sleep or freeze the clock.
`tests/test_recurrence_properties.py` gives each hypothesis example its own in-memory engine
(`fresh_db()`), because hypothesis cannot reuse function-scoped fixtures.
