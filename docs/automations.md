# Automations

ClickUp-style rules, the engine that runs them over the event log, and the run log. Rules are
set up through the REST API and the MCP tool; the board has no builder page. The feature is
frozen: the engine keeps running rules but gains no features.

## Automations

ClickUp-style "when this happens, if these hold, do that" rules.

- **Rule** (`automations` table): name, scope (`list_id`, else `section_id`, else every list),
  `trigger_json` `{type, ...}`, `conditions_json` `[{field, op, value}]` (all must hold, judged on
  the item as it is when the rule runs), `actions_json` `[{type, ...}]` (1-10), `enabled`. The
  vocabulary is `service/automations.py` `TRIGGERS`/`CONDITION_FIELDS`/`ACTIONS`, served as `GET
  /automations/catalog` so the board and agents build rules from one list. Validation names handles,
  tags, priorities and lists in plain English (handles and tag names are normalised on save).
- **Engine**: `automations.process(session, send=)` reads every event after the `engine_cursors` row
  `automations` (first start = newest event, so history is never replayed), runs the rules each one
  fires, and moves the cursor past every event, committing right after each run and once per page
  otherwise. A rule that already has a run logged for an event is not run for it again, so a
  restart between a run and the cursor's commit repeats nothing; a crash in the middle of a rule's
  actions, before its run is logged, can run that rule once more after the restart (each action
  is its own change). An event that cannot be handled at all is logged and skipped.
  `slate/automator.py` runs it in a worker thread, woken by the bus (a change usually reaches its
  rule in well under a second) with a 5 s poll as a backstop. It runs when
  `SLATE_AUTOMATIONS_ENABLED` (default on; off in pytest; on in the board e2e, which keeps the
  scheduler off).
- **Actor**: actions run as the `slate` system agent, `source=system`, `session_ref=automation:<rule
  id>`. The board's activity column says "by automation “<name>”" from the session_ref. **Events
  whose session_ref starts with `automation:` never fire rules** (ClickUp's rule too): no loops, no
  chains.
- **Each action is its own change** (`_do()` calls the ordinary service functions: `update_item`,
  `bulk_update` for moves, `add_comment`, `archive_item`), so one failing action (a status the
  item's list lacks) is logged and the rest still run. A run is `ok`, `partial` or `failed`.
- **Run log** (`automation_runs`): one row per run with per-action `{action, ok, detail}`,
  `event_id`, `triggered_by` (whose change fired it, or who pressed Run now). Bookkeeping like
  `deliveries` (`core.BOOKKEEPING_MODELS`), pruned after 30 days row by row (the write guard refuses
  bulk DELETE outside a change). `GET /automations` adds `run_count`, `last_run_at`, `last_status`,
  so it is **not** in fastlane's `CACHEABLE`.
- **`webhook` action**: a JSON POST `{automation, event, item}`, built through the rule author's
  read filter (`access.reading_as`: no link to a list the author cannot see), with
  `X-Slate-Automation` and
  the signature headers of webhook deliveries (`X-Slate-Timestamp`, `X-Slate-Signature-V2` over
  `<timestamp>.<body>`, the body-only `X-Slate-Signature`; `outbound.signed_headers`), keyed by
  the rule's own secret. It goes through `outbound.post` like a delivery: the address checked is
  the address connected to, and one deadline covers the whole exchange. The secret (`whsec_…`) is made when a rule first
  gets a webhook action (create or update), stored encrypted in `automations.secret_enc` (in
  `core.SECRET_COLUMNS`' `secret_enc`, so never in an event), returned once as `webhook_secret`, and
  replaced by `POST /automations/{id}/secret` (`automation.secret_rotated`, no secret material).
  Needs `SLATE_SECRET_KEY` (else 503). One attempt, 5 s timeout, outside any write transaction; the
  result goes in the run. URLs pass `webhooks.validate_url` (https, or http to a private host). For
  retried delivery an agent registers a real webhook subscription instead.
- **Who reads a webhook address.** An address often carries a secret, so only the rule's author
  and admins (`access.is_admin`) read it in full. Everyone else gets each `webhook` action with
  its `url` cut to `scheme://host[:port]` (`access.masked_hook_urls`), in `GET /automations`, MCP
  and the `automation.*` events (stored history through `access.event_snapshots`, live payloads
  through `EventGate.project`, webhook delivery bodies). A member who edits the rule's actions
  and sends a webhook action back with that host-only `url` keeps the hidden address (the
  webhook at the same position first, else the first on the same host); any other `url`
  replaces it.
- **Run now** (`POST /automations/{id}/run {item_id}`, MCP `run`): the actions on one item, skipping
  trigger and conditions.
- **Triggers on items in archived lists or sections, and archived items, do nothing.** `moved_in`
  fires only when the item comes from a list outside the rule's scope.
- **Board**: no automations page (`#/automations` opens Everything). The task window's activity
  names the rule behind a change ("by automation “…”") from the event's `automation:<id>`
  `session_ref`; `lib/automationref.svelte.ts` reads the rule names once (`GET /automations`) when
  an activity first needs one. The Health page lists failed runs by rule name.
- **MCP**: `slate_automation` (catalog, list, get, create, update, delete, run, runs).
- **Adding a trigger or action**: add it to `TRIGGERS` (event type, label, params) or `ACTIONS` and
  handle it in `trigger_fires()` or `_do()`; the catalog, validation, MCP schema and the board's
  builder pick it up. Param kinds the builder knows: status, status_kind, account, tag, priority,
  list, number, text, url.
