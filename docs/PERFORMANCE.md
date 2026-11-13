# Performance at scale

How Slate holds up with a big board, many agents and several boards open, what was slow, and the regression check in CI. Run it yourself with `uv run python scripts/load_test.py` (add `--browser` after `cd web && npm run build` to time the board in Chromium too).

## The load

`scripts/load_test.py` seeds a throwaway database directly: **20,000 tasks in 50 lists** (5 sections, 30 tags, 70% with due dates, half assigned across the agents, one `item.created` event each), starts a real `uvicorn` on it, then for 30 s:

- **4 boards** each do the board's first load, hold the SSE stream open and refetch what the board's reducer refetches;
- **10 agents** each loop: `GET /checkin` from their cursor (with `If-None-Match`), then a `POST /batch` of five changes on their own tasks (status, priority, title, a comment, a progress report), then pause 0.125–0.375 s;
- with `--browser`, a real Chromium opens the board during the storm and times first load, regrouping and a live change (`web/scripts/board-perf.mjs`).

Host for the numbers below: a 4-CPU Linux cloud container, Python 3.12 (one Slate process, as in production). Your server will differ; the shape will not.

## Results

| | Before | After |
|---|---:|---:|
| Agent check-in, under load (p50 / p95) | 1,849 / 2,023 ms | 55 / 172 ms |
| Agent check-in, cold start under load (p50) | 3,562 ms | 858 ms |
| `POST /batch` of 5 changes (p50 / p95) | 137 / 285 ms at 79 batches | 137 / 322 ms at 625 batches |
| Changes written per second (the agents' throughput) | 17.6 | 47.9 |
| Commit → open board over SSE (p50 / p95 / max) | 49 / 115 / 192 ms | 18 / 79 / 265 ms |
| Events a board missed | (not measured correctly, see below) | 0 of 5,752 deliveries |
| `GET /items`, every task (p50) | 2,253 ms | 162 ms |
| Board's first load, 4 at once, server side | 27 s | 2.8 s right after start, 0.2 s warm |
| `/today` the sidebar fetched on every change | 3.7 MB, 500 ms | not fetched (counted on the board) |
| Server memory, peak | 749 MB | 305 MB |
| Chromium: first load to cards, unloaded server | 5.6 s | 1.5 s |
| Chromium: first load to cards, during the load | — | 1.8 s |
| Chromium: an API change on screen, during the load | — | 334 ms |
| Chromium: regroup 20,000 tasks (priority / status / due / list) | — | 530 / 522 / 853 / 1,760 ms |

"Before" and "After" are the same harness run before and after the fixes below (for "Before", the board's `GET /items/{id}` refetches and `/today` are replayed with `LOAD_BOARD=old`). The "events missed" count for before (148) came from a harness bug (boards stopped reading before the last batches landed) and is left out. The "list" regroup paints 50 columns of 25 cards; the others paint fewer columns.

## What was slow, and the fix

1. **A GIL convoy in SQLite reads.** Python's `sqlite3` releases the GIL around every row it steps. With ten threads reading at once, each row waited for the GIL behind another thread: a 13 ms read of 20,000 rows took **12 s** with ten readers, and a check-in that took 28 ms alone took 1.8 s with ten agents. `db.SerialCursor` fetches rows under one process-wide lock, in chunks of 500, after the statement's first step has run outside it, so readers take turns instead of thrashing (the same 20,000-row read: 197 ms median with ten readers). A long aggregate never holds the lock.

   **One connection per holder.** A file database's pool hands each connection to one session at a time, and a session is used by one thread at a time (a request's sync route in the threadpool, the token check before it on the event loop, each `run_in_threadpool` step awaited in turn; every background engine, the scheduler, the MCP token check and each stream step open their own session). So in production no two threads ever use one SQLite connection at once. The in-memory database the tests use is different: `StaticPool` gives every session and thread the same connection, and sqlite3 is not safe for two threads on one connection (a read can get another's rows, raise `InterfaceError: bad parameter or other API misuse`, or crash the process). `db.SharedConnection`, the in-memory database's connection class, gives that connection one re-entrant lock (`turn`) held across each statement and its buffered fetch, each cursor close, commit, rollback and close. Transactions still share it, so one thread's rollback also undoes another thread's uncommitted write; a test that needs two threads writing independently uses a file database (`tests/test_api_events.py`'s `live`). `tests/test_shared_connection.py` hammers one in-memory engine from twelve threads.
2. **The check-in queue read every assigned task.** It loaded and sorted all of an agent's open items in Python (1,000 rows at this size) to return the top 50. It now starts from the agent's assignments, ranks in SQL (`ORDER BY` priority rank, due, created) and `LIMIT`s, with a `COUNT` for `queue_total`, helped by the index `ix_items_waiting_on_account_id`. The news half loads the agent's assignments and watches only for the items in the events it reads.
3. **Every board load rebuilt every task.** `items.views()` costs ten batch queries and a dict per task: 1.4 s for 20,000, then 0.5 s more to serialise, per board. `service/item_cache.py` keeps each task's JSON, dropped by the events that change it (see "Item cache" in `docs/fast-lane.md`); `GET /items` and `/today` read only the matching ids and join the cached bytes. The cache warms in the background at startup, and a big miss is built once even when four boards ask at the same moment.
4. **The sidebar's Today count downloaded every overdue task** (3.7 MB at this size) 300 ms after every live change. The board now counts Overdue, Today and Waiting from the tasks it already holds, in one pass that also does the per-list open counts (those were 50 passes over every task per render).
5. **The board's refetches were one `GET /items/{id}` (the full detail with history) per task.** They are one `GET /items?ids=…` per 200 now, answered from the item cache.
6. **The board loaded 20,000 tasks into its plain mirror one `$state.snapshot` at a time** (1.9 s). It now keeps the loaded objects as the mirror and gives the reactive store a copy.
7. **Each live event re-ran the views over every task** (about 100 ms at 20,000), so 40 changes a second would have pinned the page. Events are applied in batches: a lone event at once, and while they keep coming the board spends at most half its time catching up. The view engine's sort also takes each task's keys once into an array (no map lookup per comparison) and compares ISO dates as text.
8. **Smaller things:** `/stats` reports write-lock wait and hold times and item-cache hits; the per-request `X-Slate-Cursor` read has its own plain connection so the event loop never queues behind the fetch lock.

## Agent batches and check-ins

A second round on the same harness: two runs each before and after the three fixes below, medians of the two:

| | Before | After |
|---|---:|---:|
| `POST /batch` of 5 changes (p50 / p95) | 210 / 389 ms | 82 / 195 ms |
| Agent check-in with a cursor (p50 / p95) | 84 / 228 ms | 44 / 160 ms |
| Changes written per second (the agents' throughput) | 47.9 | 66.8 |
| Commit → open board over SSE (p50 / p95 / max) | 22 / 97 / 267 ms | 15 / 47 / 119 ms |
| Write lock wait (p50 / p95) | 107 / 269 ms | 1 / 68 ms |
| Write lock held (p50 / p95) | 37 / 134 ms | 21 / 43 ms |
| CI size: batch p50, check-in p50 | 64 ms, 56 ms | 41 ms, 27 ms |

What changed:

1. **Batch tasks are built after the commit.** 80% of the time a batch held the write lock went to building the task views each op returns, inside the change. Write ops now leave a mark (`batch.Later`) and the batch builds every marked task once, in one `items.views()` call, after the commit and outside the lock. The cost: when two ops in one batch touch the same task, both answer with the task's final state (`views: "each"` keeps per-op snapshots; see `docs/agents/rest.md`).
2. **Background engines wrote once per event.** The engines that follow the event log (phone push, automations) each read every new event and committed their cursor after each one, even with nothing configured to send to: one extra SQLite write transaction per engine per event, competing with the agents' commits for SQLite's single writer. With nothing to send to they now skip to the newest event in one write; otherwise they commit once per page, and right after each send so a restart never sends twice.
3. **Check-in and `fields=` reads use the item cache.** The check-in queue and `GET /items?fields=` rebuilt their task views every call; they now render from the item cache and cut the cached JSON to the asked fields (`item_cache.trim`). The queue query reads ids only.

## What is left

- **Regrouping by list** paints 50 columns of 25 cards (1.8 s under load). Painting fewer cards per column when there are many columns would help; the engine itself takes 5–15 ms at 20,000 tasks (`web/src/lib/views.perf.test.ts`).
- **An agent's first check-in** (no cursor) scans back up to 5,000 events; under load that is about 0.9 s. It happens once per agent start.

## The regression check

CI's `bench` job runs `scripts/load_test.py --ci`: 5,000 tasks, 20 lists, 5 agents, 2 boards, 15 s. `bench/budgets.json` holds upper bounds (p50/p95 per call, fan-out, zero missed events and errors, peak memory) set at several times what this container measures, so a slow runner passes and a real regression (a lost index, a convoy, a per-item query) fails. The numbers are uploaded as the `bench-result` artifact. The board's engine has its own budget in `web/src/lib/views.perf.test.ts` (each grouping of 20,000 tasks under 250 ms), which runs with the unit tests.

When a change makes Slate legitimately slower, raise the budget in the same PR and say why.
