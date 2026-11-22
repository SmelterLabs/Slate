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

The event shape and the list of every type are in [reference/events.md](reference/events.md)
(generated from the code).

## Check-in

See [quickstart.md](quickstart.md#check-in-without-claiming). Check-in lists include the filing
note (`note_md`), list kind and reference flag. The queue shows open work-list cards assigned
to you or waiting on you. It can include Later and Parked cards. Use `/work/next` for pickup
eligibility. News reasons:
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

Your check-in, pushed live. Each message has `id:` set to the event id, your resume cursor.

- `event: news`: an event that concerns you, in the check-in's news shape (`why`,
  `item_title`, the event fields). Reminders and digests can appear even when addressed to you
  by the same account.
- `event: work`: `{"id", "item_id", "type"}`: a task you could take with `POST /work/next`
  just became free, for example through assignment, unblocking, or an explicit release.
  With `pool=true`, unassigned tasks in lists you may edit count too. One hint per task during
  a replay. Chat and document mentions also send work hints. Call `/work/next` to take the work.
- `event: control`: a new question or Stop request for a card you hold. Heartbeat the card
  with its run proof to read pending messages and the stop state.

An expired lease stays quarantined until it is reconciled or cleared. Its expiry alone does
not make the task available for a new run.

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

## Due events

The scheduler writes `item.due_soon` (24 hours before), `item.due` and `item.overdue` (an hour
after) once per due date for eligible cards, as actor `slate` with source `system`. It skips
Parked and reference cards. On a card's return from parking, `item.unparked` clears the
parking fields and reaches the card's people as a reminder. See [due stages](../recurrence.md#due-events)
and [released alerts](../notifications.md#notification-level).
