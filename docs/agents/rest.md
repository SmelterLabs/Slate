# REST conventions

The rules every route follows. The route-by-route list is
[reference/rest.md](reference/rest.md) (generated); the live schema is `/openapi.json` and the
interactive page `/docs` on a running Slate.

## Basics

- Base URL `https://slate.example.com/api/v1` (your server's address). JSON in and out.
- Auth `Authorization: Bearer slate_<id>_<secret>` ([auth.md](auth.md)).
- `X-Slate-Session-Ref: <your run id>` (optional, up to 200 chars) is stored on every event
  the request writes. Send it; it is how a person traces a change back to the run that made it.
- Ids are 12-character base62 strings. Event ids are time-ordered, so they double as cursors.
- Times are UTC ISO 8601 in answers. In requests a date may be `YYYY-MM-DD` (17:00 that day in
  the workspace timezone) or ISO 8601; a timestamp with no offset is read as workspace time.
  `GET /me` names the workspace timezone.
- `PATCH` changes only the fields you send; `null` clears one; list fields (`assignees`,
  `tags`) replace the whole set.

## Errors

Every error is `{"detail": "..."}` with plain-English text you can show or log.

| Status | Means |
|---|---|
| `401` | no token, or it is invalid, revoked, or its account is disabled |
| `403` | the token's scope is too low, or the thing is not yours to change |
| `404` | no such id (or not visible to you) |
| `409` | a conflict: a taken handle, or a claim someone else holds |
| `410` | gone: `POST /bootstrap` after the first account |
| `422` | the request is invalid; FastAPI validation errors give `detail` as a list of `{loc, msg}` |
| `429` | rate limited: wait `Retry-After` seconds |
| `503` | a feature that needs configuration is off (webhooks without `SLATE_SECRET_KEY`) |

A failed `POST /batch` op adds `"failed_op": <index>` to the body.

## Read less

| Want | Ask |
|---|---|
| Only some fields | `GET /items?fields=id,title,status,due_at` (id is always included; an unknown field is a `422` that lists the real ones) |
| A page | `limit=1..1000&offset=N` (order: position, then created) |
| Only what changed | `GET /items?changed_since=<event id>` (a previous `X-Slate-Cursor` or `next_since`) |
| Nothing if nothing changed | send the last `ETag` back as `If-None-Match`; an unchanged answer is `304` with no body |
| Smaller bytes | send `Accept-Encoding: gzip`; JSON over 1 KB is compressed |

`ETag`s cover the reads that depend only on stored state: items (and their events, versions,
activity, watchers, comments, checklists, attachments, links), lists, sections, tags, views,
accounts, favorites, `me` and `checkin`. The generated
reference marks each one. `/today`, `stale=true`, recurrences, webhooks, token lists and usage
are not tagged, because their answer also depends on the clock or on bookkeeping.

A `304` is answered before the token is even checked against the rate limiter, so polling
with `If-None-Match` is close to free.

## Snapshot, then follow

1. **Snapshot.** `GET /items?assignee=<me>&fields=id,title,status,due_at`. Every `GET`
   carries `X-Slate-Cursor`: the newest event id when the read began.
2. **Follow** from that cursor: the SSE stream `GET /events/stream?since=<cursor>`, polling
   `GET /events?since=<cursor>` (keep `next_since`), or
   `GET /items?changed_since=<cursor>&include_archived=true`.

Every change is an event, so "events after the cursor" is a complete answer: you never miss a
change between the snapshot and the follow (at worst you see one twice). See
[events.md](events.md).

## Many changes in one round trip: `POST /batch`

```json
POST /api/v1/batch
{"ops": [
  {"ref": "t", "tool": "item", "action": "create",
   "params": {"list_id": "Ab12Cd34Ef56", "title": "Renew passport", "due_at": "2026-11-01"}},
  {"tool": "item", "action": "assign", "params": {"id": "$t.id", "assignees": ["my-agent"]}},
  {"tool": "comment", "action": "add", "params": {"id": "$t.id", "body_md": "On it"}}
]}
```

- An op is exactly an MCP call: `tool` (with or without `slate_`), `action` (omit it for
  single-action tools such as `today`), `params`. [reference/mcp.md](reference/mcp.md) is the
  catalogue.
- `ref` names an op's result; a later string param that is exactly `$ref` or `$ref.path`
  (`$t.id`, `$t.status.name`, `$t.assignees.0.handle`) is replaced by it.
- **Atomic by default:** one transaction. If any op fails nothing is written, and the answer
  is that op's status with `{"detail": "op 2: ...", "failed_op": 2}`.
- `"atomic": false` commits each op on its own and reports every slot as
  `{ok: true, result}` or `{ok: false, error, status}`.
- Up to 100 ops. Each op needs its own scope (a read token can batch reads). A batch cannot
  contain a webhook test, another batch or a long-poll (an op with `wait` above 0: check-in,
  `work next`, `work finish` with `next.wait`). It counts as one request for rate limiting.

### Which state of a task a result shows: `views`

A write op that answers with a task (`create`, `update`, `set_status`, `assign`, `report`,
`bulk_update`, ...) answers with **the task as the whole batch left it**. Slate builds those
tasks once, after the commit, for all ops together, which makes a batch about twice as fast
under load. So when two ops in one batch touch the same task, both slots show the same, final
task:

```json
{"ops": [
  {"tool": "item", "action": "update", "params": {"id": "Ab12", "priority": "high"}},
  {"tool": "item", "action": "update", "params": {"id": "Ab12", "title": "Book trains"}}
]}
→ both results: {"id": "Ab12", "title": "Book trains", "priority": "high", ...}
```

- `$ref` lookups still see the state at their point: `$t.status.name` is the status right
  after op `t`, not after the batch. `$t.id` never costs a view.
- Read ops (`get`, `query`, `checkin`, ...) answer with what they saw at their point in the
  batch.
- A task deleted for good later in the same batch (`trash` then `purge`) answers `null`.
- `"views": "each"` makes every slot show the task as that op left it.
  It is slower (an atomic batch builds each task while holding the write lock), so use it only
  when you need the in-between states.

Filing several items in a batch is several separate creates; Slate never makes subtasks unless
you `split` or pass `parent_id`.

## Timing

Every `/api/v1` and `/mcp` response carries `Server-Timing: app;dur=<ms>` (time inside Slate to
the first byte). `GET /stats` gives p50/p95/p99/max per route over the last 1,000 requests of
each, plus item, event and account counts, open live streams and the database size. Measured
numbers are in [latency.md](latency.md).
