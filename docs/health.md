# Health and observability

The Health page, `GET /api/v1/health`, the in-memory counters, poller alerts and what the
report reads from the database.

## Health and observability

The Health page (`#/health`, `components/health/HealthPage.svelte`, owners and admins) and `GET
/api/v1/health` / MCP `slate_health` read `service/health.py report()`; `GET /health` (slate.app, no
auth) is `summary()`: status and per-check status, no details, HTTP 503 when the status is `fail`.
`/healthz` is the separate deploy check. The report has no table of its own.

- **Counters live in memory** (`slate/monitor.py`) and start over on restart. `calls`: per token,
  one-minute buckets over the last 60 minutes (calls, 4xx, 5xx, slow, time), plus the last 50 errors
  and slow calls. REST calls are recorded by `fastlane.Timing` from `request.state.caller`, which
  `api.auth` sets once the token resolves (so a 403 for a missing scope and a 429 count; a bad token
  does not). In-process MCP tool calls are recorded by `mcp_server.LocalBackend` (a
  ServiceError/ToolError is 400, any other exception 500, a refused rate limit 429); stdio MCP goes
  through REST and is counted there. Slow = `SLATE_HEALTH_SLOW_MS` (default 1000).
- **Pollers.** `scheduler.tick` reports every hook to `monitor.pollers` under the key in
  `scheduler.POLLERS` (labels and expected cadence in `health.POLLERS`): a raise, or a result with
  non-empty `errors` or a truthy `error`, is a failure; a None result means the hook did not run
  this tick. A run in progress longer than 5 minutes is "stuck"; no run for 3 cadences (5 min at
  least) is overdue. Everything shows "off" with the scheduler off (tests, e2e).
- **Alerts.** When a poller's failures in a row reach `SLATE_HEALTH_ALERT_AFTER` (default 3) the
  tick records one `health.alert` (actor `slate`, source `system`, `after` = check, label, failures,
  error, last_ok_at); the first success after that records `health.recovered`. Inbox reason `health`
  goes to owners and admins only (`inbox._sees_health`), push reason `health` is on by default (a
  saved `push_reasons` list without it stays as saved). The events have no item and no subject, so
  the stream gate hides them from guests and limited tokens; `item_cache` treats `health.` as not
  touching items.
- **Engines.** `app.lifespan` registers each asyncio task with `monitor.track` (scheduler, webhooks,
  automations, push); a done task shows as stopped with its exception. Webhooks off for want of
  `SLATE_SECRET_KEY` is a warning; off by setting is just off.
- **From the database:** failed/partial `automation_runs` in the last day, webhook deliveries
  pending and failed today, engine lag (events after each `engine_cursors` row, counted up to
  10,000), database + WAL size, free disk (warn under 1 GB, fail under 200 MB).
- **Backups.** The deploy pack's `backup.sh` records every run in `backup-status.json` next to
  the database (`ok`, `at`, `detail`, `last_ok_at`). With that file present the report has a
  `backups` section and a Backups check: fail when the last run failed or the last good backup is
  older than 36 hours, warn when the file is unreadable. Without the file (no deploy pack) there is
  no line.
- **Latency**: `fastlane.stats.overall()` merges the kept samples of every route (the same
  1,000-per-route samples `/stats` uses).
- **The board** reloads the report on a `health.*` event at once and after any other event at most
  every 15 s (no polling, rule 6), plus a Refresh button. Cmd+K "Go to Health" and the nav link are
  hidden from non-admins (`board.canManage`); the server refuses them with 403.
