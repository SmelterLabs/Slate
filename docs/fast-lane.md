# Fast lane, batch and performance at scale

The internals behind the agent-facing latency numbers in `docs/agents/latency.md` and the
measured story in `docs/PERFORMANCE.md`: ETags from the event cursor, projections, batch with
views after commit, the serial cursor, the item cache and the regression budgets.

## Fast lane and batch (agent speed)

The agent-facing guide (which calls to make, measured latencies) is **`docs/agents/`** (start at its
README). The internals:

- **`src/slate/fastlane.py`**, installed last in `create_app()` (so outermost): `Timing` (pure ASGI;
  `Server-Timing: app;dur=` on `/api/v1` and `/mcp`, last 1,000 samples per route template in
  memory, read by `GET /api/v1/stats` in `api/stats.py`), Starlette's GZip at level 1 (min 1 KB; SSE
  is excluded by Starlette), and `Conditional`: every API `GET` gets `X-Slate-Cursor` (max event id,
  read on the event loop through `engine.connect()` before the route runs) and the reads matched by
  `CACHEABLE` get a weak `ETag` = hash(cursor, path, query, Authorization). A matching
  `If-None-Match` from an authenticated caller is a `304` before the route runs. This is only
  correct because every visible change is an event (rule 3): **a new GET route whose answer depends
  on the clock or on bookkeeping rows (token `last_used_at`, deliveries) must stay out of
  `CACHEABLE`**; a new event-backed read can join it.
- **`GET /items` extras** (`api/items.py`, `service/items.query_items`): `fields` (projection,
  validated against `ItemOut`; `parse_fields()` is shared with MCP), `limit`/`offset`,
  `changed_since` (items with any event id > cursor: `Item.id IN (SELECT item_id FROM events WHERE
  id > ?)`). The route returns a `Response` serialised in one `TypeAdapter(list[ItemOut]).dump_json`
  call instead of FastAPI's per-route path; `response_model` stays for OpenAPI.
  `tests/test_fastlane.py` checks the full shape still equals `ItemOut`.
- **`src/slate/batch.py`** + `api/batch.py` + MCP `slate_batch`: an op is an MCP call run through
  the tool table's `local` binding (`mcp_tools.TOOLS`), so batch has no action table of its own: **a
  new MCP action is batchable for free**. Atomic mode wraps every op in one outer `apply_change()`
  (inner service blocks join it; one commit; publishes only on success); `BatchFailed(index, cause)`
  maps to the cause's HTTP status plus `failed_op` (`api.status_of()`). Non-atomic rolls back the
  failed op only. `EXCLUDED` holds actions that must not run under the write lock (webhook test does
  network I/O) and nested batches. `$ref.path` substitution only replaces strings that are exactly a
  ref expression *and* name an earlier ref; anything else stays literal. **Views after commit**
  (`views`, default `"final"`): `_run_one` gives write actions a `Local` whose `later` list is set,
  so `mcp_tools._item_views()` returns `batch.Later(id)` marks instead of building `ItemOut` dicts;
  `batch._finish()` builds every marked task with one `items.views()` call after the commit (outside
  the write lock) and swaps the marks in. A `$ref` path into a mark builds that one view on the spot
  (`.id` needs none), so refs keep their at-that-op meaning. Read actions and helpers that
  post-process a view (`_links_view`, `query` with `fields`) pass `now=True`. `"each"` turns the
  marks off (per-op snapshots). A new action that answers with `_item_view(s)` is deferred
  for free; one that reads keys out of the view must pass `now=True`.
- **Fewer thread hops:** `db.get_session` and `auth.require()` are `async` (a sync dependency costs
  a worker-thread round trip each, ~0.3 ms). Token lookup is two primary-key reads on the loop;
  `accounts.resolve_token(touch=False)` + `needs_touch()`/`touch_token()` keep the once-a-minute
  `last_used_at` write in the threadpool so a busy writer never blocks the loop. Routes stay sync
  (they write; writes take the process write lock). `GET /me` went from ~4.2 to ~2.3 ms.
- **Measured hot spots fixed:** `UTCDateTime` parses with `datetime.fromisoformat` (strptime was
  ~40% of a 2,000-item list read); `links.links_of` builds its `aliased()` constructs once at import
  and skips the blocker query when no item has blockers (aliasing copied every column, ~2 ms per
  item view).
- **Benchmark:** `uv run python scripts/bench_agent.py [--items N]` starts a real server on a
  temp database and prints p50/p95/p99 for the agent loop and the owner's common calls. The load test is `scripts/load_test.py` (see
  "Performance at scale").

## Performance at scale

Measured numbers and the story are in `docs/PERFORMANCE.md`. What a fresh agent must know:

- **`db.SerialCursor`.** Python's `sqlite3` releases the GIL per stepped row, so concurrent readers
  convoy (10 threads made a 13 ms read take 12 s). Every engine connection is a `SerialConnection`
  whose cursor runs the statement's first step outside the lock, then fetches all rows in 500-row
  chunks under `db.STATEMENT_LOCK` into a buffer. Results are therefore fully buffered; do not rely
  on streaming a huge result row by row. `fastlane.current_cursor()` uses its own plain `sqlite3`
  connection (outside the lock) so the event loop never waits behind it; `:memory:` databases fall
  back to the engine.
- **Item cache** (`service/item_cache.py`, the module-level `cache`). Holds each item's `ItemOut`
  JSON bytes. `core.apply_change` calls `cache.invalidate(session, events)` after commit, under the
  write lock, before publishing. Rules: event types in `NO_EFFECT` change nothing an item view
  shows; item-scoped events (`item.*`, `comment.*`, `checklist.*` with an `item_id`) drop the item,
  its parent, any parent named in the snapshots or a `parent_id` change, and every item whose view
  names it (blockers, `_near`/`_rev` index); **any other event type clears the whole cache**, so a
  new event type is safe by default and only needs classifying if it is hot. `begin()` returns a
  sequence token and clears the cache when the newest event id moved without passing through
  `invalidate` (another process wrote, e.g. the CLI); `put()` refuses views built from a snapshot
  older than an invalidation. Misses build in 2,000-item chunks, single-flight above 200 misses.
  `app.lifespan` warms it in a background thread. `fields=` reads (`GET /items?fields=`,
  `/checkin?fields=`) use it too: `trim()` loads each cached view and keeps the wanted keys, about
  20x cheaper than rebuilding the view. Tests run with `item_cache.VERIFY = True`
  (every hit is compared with a fresh build and raises `StaleView`), and `tests/test_item_cache.py`
  checks each kind of change.
- **`GET /items?ids=a,b,…`** (up to 1,000) is the board's refetch; ids missing from the answer are
  gone or not visible. `GET /today?fields=id` returns ids only.
- **`/stats`** adds `write_lock` (wait/held p50/p95/max over the last 2,000 changes) and
  `item_cache` (size, hits, misses).
- **Check-in queue** ranks and limits in SQL from the agent's assignments and
  `waiting_on_account_id` (indexed). Keep it that way; a Python sort over every assigned item was
  the 1.8 s check-in.
- **Regression check:** CI's `bench` job runs `scripts/load_test.py --ci` against
  `bench/budgets.json` and fails when a budget is broken. `--browser` (after `npm run build`) also
  runs `web/scripts/board-perf.mjs`. `web/src/lib/views.perf.test.ts` budgets the board's view
  engine at 20,000 items.
