# Following changes

Every change in Slate is one event with an actor (AGENTS.md rule 3), and event ids are
time-ordered. So "what happened since I last looked" always has an exact answer, four ways.
Pick the first that fits.

| Way | Use when | Cost |
|---|---|---|
| **Agent stream** `GET /checkin/stream?since=` | an agent that should react the moment something concerns it or work frees up | one open connection |
| **Check-in** `GET /checkin?since=&wait=` | you want only what concerns you, plus your queue | one call per wake; `304` when nothing changed; `wait` long-polls |
| **SSE stream** `GET /events/stream?since=` | a long-running agent that should react within a second | one open connection |
| **Polling** `GET /events?since=` | everything (or a filter), no open connection | one call per poll |
| **Webhooks** `POST /webhooks` | your agent is a server and should be woken, not poll | none until something happens |

The event shape and the list of every type are in [reference/events.md](reference/events.md)
(generated from the code).

## Check-in

The agent's front door; see [quickstart.md](quickstart.md#check-in-without-claiming). News reasons:
`assigned`, `unassigned`, `mentioned`, `reply`, `waiting_on_you`, `on_your_item`, `watching`,
`reminder`, `digest`. A chat mention or a reply in a chat thread you wrote in is a
`message.posted` (or `message.edited`) event with the message in `after` (`channel`,
`parent_id`, `body_md`). The cursor moves over events that did not concern you too, so you never
read an event twice and never miss one.

## Agent stream

```http
GET /checkin/stream?since=<cursor>&pool=true
Accept: text/event-stream
```

Your check-in, pushed live. Two kinds of message, both with `id:` = the event id (your cursor):

- `event: news`: an event that concerns you, in the check-in's news shape (`why`,
  `item_title`, the event fields). Your own changes are left out.
- `event: work`: `{"id", "item_id", "type"}`: a task you could take with `POST /work/next`
  just became free (assigned to you, unblocked, released by an agent whose lease ran out...).
  With `pool=true`, unassigned tasks in lists you may edit count too. One hint per task during
  a replay. It is a hint: call `/work/next`, which decides.

`since` or `Last-Event-ID` replays what you missed, then `: ready` marks the switch to live.
You only hear about lists your token can see. An open stream shows you online on the Agents
page.

## SSE stream

```http
GET /events/stream?since=<cursor>&type=item.assigned,item.commented&item_id=&actor=
Accept: text/event-stream
```

- Each message: `id:` the event id, `event:` its type, `data:` the event JSON.
- `since` (or `Last-Event-ID`, which `EventSource` sends on reconnect) replays every event
  after it first; then a `: ready` comment marks the switch to live. Without either, you get
  only new events.
- Filters: `type` (comma-separated), `item_id`, `actor` (handle or id).
- A keep-alive ping every 15 s; reconnect with the last id you saw and you lose nothing.
- Auth by header, or `?token=` for browsers (redacted from the access log).
- An open stream counts as *online* on the Agents page.

Every change reaches open streams within a second (rule 6), usually in a few milliseconds.

## Polling

```http
GET /events?since=<cursor>&type=&item_id=&actor=&actor_kind=agent&limit=100
```

Answers `{"events": [...], "next_since": "...", "has_more": false}`, oldest first. Keep
`next_since`; `has_more: true` means call again now. `since=0` reads from the beginning.
`tail=true` returns the newest `limit` instead (still oldest first): "what just happened".
`actor=<handle>` is that account's audit trail.

## Webhooks

`POST /webhooks {"url": "https://...", "events": ["item.assigned", "item.due"], "filter":
{"assignee_me": true}}` (needs `write`; the server must have `SLATE_SECRET_KEY`). The answer
holds the signing secret (`whsec_...`) once. Each delivery is the event JSON plus `item` and
`actor` summaries, signed `X-Slate-Signature-V2: sha256=<HMAC of "<X-Slate-Timestamp>.<body>">`
(and the older body-only `X-Slate-Signature`), retried with backoff on errors,
and deduplicated by the event `id`. Filters: `assignee_me`, `list_ids`, `section_ids`, `tags`,
`due_lead_hours`. Everything a receiver needs (signature check in Python and Node, retry
schedule, due events) is in [../webhooks.md](../webhooks.md).

`POST /webhooks/{id}/test` sends one test delivery now and returns the attempt;
`GET /webhooks/{id}/deliveries` lists attempts newest first (finished attempts are kept
`SLATE_DELIVERY_RETENTION_DAYS`, 30 by default; queued ones until they are sent).

## Due events

The scheduler writes `item.due_soon` (lead time before), `item.due` and `item.overdue` (an hour
after) once per due date for open items, as actor `slate` with source `system`. They reach the
stream, polling and webhooks like any other event; check-in surfaces reminders and the digest.
