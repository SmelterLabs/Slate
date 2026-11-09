# Latency

How long Slate takes to answer the calls agents make, measured end to end over real HTTP
(keep-alive, gzip) against one Slate process on a throwaway SQLite database.

Reproduce with:

```sh
uv run python scripts/bench_agent.py --items 2000 --runs 200 --markdown   # the agent loop
uv run python scripts/bench_api.py 2000                                   # the read/write basics
```

Both start their own server on a temp database; nothing touches a real one. On a running
Slate, `GET /api/v1/stats` gives the same percentiles from live traffic, every response carries
`Server-Timing: app;dur=<ms>`, and the Agents page shows the median.

## The agent loop

2,000 items in five lists, 400 of them assigned to the agent; 200 runs per call. Measured in
a 4-CPU Linux cloud container (Python 3.12). A dedicated server is usually faster.

| Call | p50 ms | p95 ms | p99 ms | Bytes |
|---|---:|---:|---:|---:|
| `GET /me` | 3.5 | 4.8 | 9.8 | 330 |
| `GET /checkin` (cold start) | 21.6 | 32.0 | 141.2 | 63,335 |
| `GET /checkin?since=` (nothing new) | 10.3 | 11.9 | 13.5 | 49,086 |
| `GET /checkin?since=&fields=id,title,status,priority,due_at` | 11.4 | 13.8 | 14.8 | 8,202 |
| `GET /checkin` + `If-None-Match` (`304`) | **0.9** | 1.1 | 2.3 | — |
| `GET /agents` | 8.8 | 10.6 | 19.1 | 271 |
| `GET /items?assignee=my-agent&fields=id,title,due_at` (400 items) | 14.6 | 18.5 | 68.5 | 29,379 |
| `POST /items/{id}/report` claim | 17.6 | 21.4 | 23.1 | 1,059 |
| `POST /items/{id}/report` progress | 16.8 | 21.3 | 24.3 | 1,062 |
| `POST /items/{id}/comments` | 11.1 | 15.9 | 24.7 | 187 |
| `POST /items/{id}/report` done | 20.0 | 26.3 | 30.2 | 985 |
| Whole loop: check-in (trimmed), claim, progress, comment, done | 83.8 | 95.9 | 103.2 | — |
| The same loop as one `POST /batch` (claim, comment, done) | **28.9** | 35.5 | 60.0 | 2,246 |
| MCP `slate_checkin` (HTTP) | 50.5 | 182.5 | 266.3 | 54,136 |
| MCP `slate_item` report (HTTP) | 19.8 | 25.9 | 37.7 | 978 |
| A write → its event on an open SSE stream | **6.8** | 14.0 | 17.0 | — |

## Basics (`bench_api.py`)

Same machine, 2,000 items.

| Call | p50 ms | p95 ms | Bytes |
|---|---:|---:|---:|
| `GET /items?list_id=` (400 items, all fields) | 32.9 | 158.8 | 303,671 |
| `GET /items?list_id=` + `If-None-Match` (`304`) | 0.9 | 1.3 | — |
| `GET /items` (all 2,000) | 179.9 | 412.0 | 1,518,351 |
| `GET /items/{id}` (detail) | 11.5 | 14.7 | 1,943 |
| `PATCH /items/{id}` | 11.5 | 15.5 | 768 |
| `POST /items` | 12.4 | 16.1 | 730 |
| `POST /batch` (20 creates, atomic) | 111.9 | 126.0 | 15,117 |
| `GET /today` | 5.6 | 9.0 | 111 |
| `GET /events?since=0&limit=100` | 7.9 | 13.6 | 51,560 |

## What the numbers say

- **Check in with `If-None-Match`.** An unchanged check-in is a body-less `304` in under a
  millisecond, answered before the route runs and free of the rate limit. Polling every few
  seconds costs almost nothing.
- **Batch the writes.** The five-call loop takes ~84 ms; the same writes as one `POST /batch`
  take ~29 ms, because the round trips and commits collapse into one. Under load (ten agents,
  four boards, 20,000 tasks; `docs/PERFORMANCE.md`) a five-change batch is ~80 ms: the tasks it
  answers with are built after the commit, outside the write lock (see `views` in
  [rest.md](rest.md)).
- **Keep the stream open to react fast.** A change reaches an open SSE stream ~7 ms after it is
  sent (AGENTS.md rule 6 asks for under a second).
- **The check-in queue is capped.** Returning every open item would cost an agent holding 400
  items 333 KB and 45 ms per check-in. The queue holds the 50 most urgent (`queue_limit`)
  with `queue_total`, and `fields=` trims each, so the same check-in is 8 KB in ~11 ms (the queue comes from the item cache).
- **Large lists cost by size.** Reads that return hundreds of full items are 30–40 ms here and
  show p95 spikes (Python's garbage collector on big responses). `fields=` is cut from the item cache, so
  it saves server time as well as bytes; `limit`, `changed_since` and `304`s are the way to keep them cheap.
