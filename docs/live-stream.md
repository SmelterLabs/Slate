# Live event stream and polling

The SSE stream and its polling fallback: the write-lock argument that makes
`id > cursor` a complete answer, the replay/subscribe seam, slow consumers and how the stream is
tested. The board's side is `docs/board.md`; the agent-facing page is `docs/agents/events.md`.

## Live event stream and polling

Module: `src/slate/api/events.py`, mounted from `app.py` by `events.install(app)` (router under
`/api/v1`, plus the access-log filter below).

- **`GET /api/v1/events/stream`** (SSE, `sse-starlette`). One message per event: `id` = event id,
  `event` = event type, `data` = `core.event_payload()` as JSON. Query: `since` (event id; `0` =
  everything), `type` (comma list, exact match), `item_id`. The `Last-Event-ID` header, sent by
  EventSource on reconnect, beats `since` (the reconnect URL still carries the old `since`). After
  any replay the server sends a `: ready` comment carrying `retry: 2000`; tests and clients can
  treat it as "now live". sse-starlette pings every 15 s; `send_timeout=30` drops a client whose
  socket stops draining.
- **`GET /api/v1/events`** (polling fallback): `since`, `type`, `item_id`, `limit` (default 100,
  1–1000). Returns `{events, next_since, has_more}`; pass `next_since` back as `since`. Header auth
  only.

**Why "id > cursor" is a complete answer.** `apply_change()` holds a process-wide `threading.RLock`
(`core._write_lock`) from the start of the outermost block to the end of its publishes. Event ids
are chosen at flush, so with the lock they are assigned, committed and published in one order.
Without it, an event-only transaction (no earlier write to take SQLite's write lock first) could
pick an id, lose the race to SQLite's lock, and commit after a later id: a cursor client would then
skip it. `tests/test_write_order.py` reproduces that race (it fails without the lock). SQLite
already serialises writers, so the lock costs nothing in throughput; it does mean **never `await` or
block on I/O inside an `apply_change()` block**.

**Replay/subscribe race.** The stream (`_stream`) does, in order: (1) subscribe to the bus; (2)
replay `id > since` from the table in pages of 1000, or with no `since` take the newest id as
cursor; (3) live: drop any bus message with `id <= cursor` (it was already in the replay), send the
rest, advance the cursor. A change committed before the replay query is in the table; one published
after the subscribe is in the queue; one that is both is skipped by the cursor.
`test_catch_up_has_no_gap_or_duplicate_across_the_subscribe_replay_seam` injects writes on both
sides of the query.

**Slow consumers.** Bus queues are bounded (1000). A full queue drops its *oldest* message, logs one
warning per burst and counts `Subscription.dropped`; `publish()` never waits. The stream checks
`dropped` before sending each live message and, if set, replays `id > cursor` from the table instead
of trusting the queue. So a slow client costs a database read, never a gap, and never stalls a
writer.

**Disconnect.** sse-starlette cancels the generator on client disconnect; the `async with
bus.subscribe()` exits and unsubscribes.

**Rechecks and limits.** Before each live message `_gate_check` rereads the token and the
account, so a revoked or expired token or a disabled account is cut off at the next event. The
caller's whole reach (`access.compute`) is recomputed only when the event may change it
(`EventGate.needs_reload`: shares, role changes, lists, sections) or `GATE_TTL_SECONDS` (10) after
the last computation, not for every message on every stream. A token holds at most
`MAX_STREAMS_PER_TOKEN` (10) open streams (`/events/stream` and `/checkin/stream` together) in
the process; one more is a 429 before the stream starts.

**Token in the query string.** `?token=` exists only because browsers' EventSource cannot set
headers; the polling endpoint does not accept it. It goes through the same
`accounts.resolve_token()` as the header (header wins if both). Auth runs as a dependency in its own
short-lived session before the response starts, so failures are ordinary 401/403 JSON, never an
opened stream. `RedactTokenQuery` (installed on the `uvicorn.access` logger) replaces the value with
`REDACTED` in access-log lines. Anything else that logs URLs (a reverse proxy, Tailscale Serve) is
outside Slate; keep their access logs off or redacted too.

**Testing SSE.** httpx's `ASGITransport` and Starlette's `TestClient` wait for the full body, so an
endless stream hangs them. `tests/test_api_events.py` runs a real uvicorn server in a thread (port
0) against a temp-file database (`slate.db.SessionLocal` monkeypatched); the shared in-memory
database would put the server's and the test's sessions on one connection. The events module
therefore reads `db.SessionLocal` at call time, not at import.
