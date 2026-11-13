# Webhooks and due events

How Slate tells agents about changes without them holding a stream open. Code: `service/webhooks.py`
(subscriptions), `service/deliveries.py` (queue), `notify.py` (HTTP + dispatcher loop),
`service/due.py` (due events), `scheduler.py` (tick hooks), `api/webhooks.py` (REST), `crypto.py`
(secret encryption).

## The secret-key rule

- Webhook secrets are **encrypted, not hashed**: Slate needs the plaintext to sign every delivery.
  Tokens stay hashed.
- `SLATE_SECRET_KEY` = 32+ random bytes as hex or base64 (`python -c "import secrets;
  print(secrets.token_hex(32))"`). The AES-256-GCM key is `sha256("slate-webhook-secrets\0" + raw)`;
  the webhook id is the associated data, so a ciphertext moved to another row will not decrypt.
  Stored as `webhooks.secret_enc = "v1:" + base64(nonce || ciphertext+tag)`.
- **Unset or too short:** the app still starts, logs `webhooks disabled: ...` and does not start the
  dispatcher; `POST /webhooks` answers 503. Tests set a fixed key in `conftest.py`.
- **Changed or lost key:** every stored secret becomes unreadable. Deliveries fail as final with
  "stored webhook secret does not decrypt"; subscribers must be re-created. Keep the key in the
  deploy's `.env`/secret store, never in the repo, and back it up with the database.
- The plaintext (`whsec_...`) appears in exactly one response, `POST /api/v1/webhooks`. `secret_enc`
  is in `core.SECRET_COLUMNS`, so no event payload carries it; `webhooks.view()` is the only shape
  routers return.

## Subscriptions

`{url, events, filter}` owned by the creating account; managed by it or an admin (others get 404).
`GET /webhooks` lists your own; an admin sees all.

- **url:** `https://` to a public host. Private targets (a non-global IP: private, loopback,
  link-local, CGNAT/tailnet `100.64/10`, IPv6 ULA; `localhost`; a bare single-label name such as
  `nas` or a docker service name; a Tailscale MagicDNS name; a name ending `.local`, `.internal`, `.lan`,
  `.home.arpa`) are refused unless the server runs with `SLATE_WEBHOOK_PRIVATE_TARGETS=true`, and
  then plain `http://` is allowed to them too. The name is judged as written when saved and resolved
  once before every send: a public name that resolves to a private address fails the delivery for
  good, and a name that does not resolve is a network error (retried), never a pass. The
  connection goes to exactly the addresses that were checked (`slate/outbound.py`), keeping the
  URL's name as the Host header and the TLS server name, so a name that answers differently the
  second time (DNS rebinding) cannot reach another host. Delivery errors carry the HTTP status,
  never the receiver's response body. `webhooks.validate_url` is the check at save and
  `check_target()` the one at send; automation `webhook` actions and Web Push use the same.
- **`slate check-targets`** (`cli.check_targets`, `webhooks.private_targets`): lists every live
  webhook and enabled automation `webhook` action whose URL names a private address and exits 1 if
  any, so a deploy can stop and decide (`deploy/server/RUNBOOK.md`, "Before a deploy that changes
  what webhooks may reach"). Judged by the name, like `validate_url`.
- **events:** a list of types (`^[a-z_]+\.[a-z_]+$`, not checked against a fixed list, so any
  event type can be named) or `["*"]`. `*` means every type **except `webhook.*`** (so a
  subscription never hears about subscriptions).
- **filter** (all present conditions must hold; stored normalized, empty ones dropped):
  - `assignee_me: true` — the owner is an assignee of the item as it stands when the event is fanned
    out (immediately after commit). For `item.assigned`, being in `before` also counts, so you hear
    that you were taken off.
  - `section_ids`, `list_ids` — the item's list / its list's section.
  - `tags` — any overlap (names normalized like tags).
  - `due_lead_hours` (1–720, default 24) — which `item.due_soon` this subscription receives.
  - Any item condition excludes events that have no item (section, account, test...).
- PATCH replaces `filter` whole; `disabled: true|false` disables/enables (events
  `webhook.disabled|enabled`).

## Delivery design

```
apply_change() commits ──► bus.publish ──(doorbell)──► Dispatcher loop (asyncio task, lifespan)
                                                         │ fan_out: events table, id > cursor
                                                         ▼
                                     deliveries rows (pending, next_at) ◄── durable queue
                                                         │ claim_due → sending
                                                         ▼
                                  notify.attempt (worker thread): sign, POST, record_attempt
                                         ok | failed | retry (+ new pending row at now+delay)
```

- **Body** = the flat `core.event_payload()` shape the SSE stream sends (`id, type, item_id,
  subject_id, actor_id, source, field, before, after, session_ref, at`) plus `item` (compact summary
  at event time: `id, title, list_id, status` name, `priority, due_at, assignees` handles, `tags`;
  or `null`) and `actor` (`{id, handle, kind}`). One shape everywhere: webhooks have no nested
  shape of their own. Built once per event at fan-out and stored in
  `deliveries.body`, so retries send identical bytes.
- **Headers:** `Content-Type: application/json`, `X-Slate-Event: <type>`, `X-Slate-Delivery:
  <attempt row id>` (new per attempt; dedupe on the body's `id` = event id), `X-Slate-Timestamp:
  <Unix seconds>`, `X-Slate-Signature-V2: sha256=<hex HMAC-SHA256(secret, "<timestamp>." + raw
  body)>` (preferred: reject a timestamp more than five minutes old and a captured delivery
  cannot be replayed later), `X-Slate-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>`
  (body only, still sent for receivers that check it), `User-Agent: Slate/<version>`.
  `outbound.signed_headers` makes the three signature headers for every sender.
- **Timeout** `SLATE_WEBHOOK_TIMEOUT_SECONDS` (10), one wall-clock deadline for the whole exchange:
  connecting, TLS, the response headers and the body (`outbound.post` gives every socket read and
  write only what is left of it, so a receiver that drips one byte at a time is cut off at the
  deadline and the attempt is a network error, retried). Redirects are not followed (a 3xx is a
  final failure).
- **Responses are drained, never kept** (`outbound.post`, shared by webhooks, automation webhooks
  and Web Push): streamed, at most 64 KB, and the delivery error carries the status only
  (echoing the body would let a subscriber read whatever its URL points at). `attempt()` records
  *any* exception as the attempt's error, not only `httpx.HTTPError`, so an unexpected failure (an
  IDNA error in the host name, say) cannot leave a row `sending` for ever.
- **Outcomes:** 2xx `ok`. Network error, 5xx or 429 → `retry` and a new `pending` row after the next
  delay. Any other status → `failed` (final). After the last delay the next failure is `failed`.
  Default `SLATE_WEBHOOK_RETRY_SECONDS=[10,60,300,1800,7200]` (a JSON list): one attempt plus five
  retries, six rows at most.
- **One row per attempt** (`attempt`, `status_code`, `response_ms`, `error` ≤500 chars, `at` = when
  tried). `GET /webhooks/{id}/deliveries?limit=&offset=` pages newest first. The signed body is
  stored once per (webhook, event): a `retry` row hands it to the next attempt's row and keeps `""`.
- **Retention:** finished rows (`ok`, `failed`, `skipped`, `retry`) older than
  `SLATE_DELIVERY_RETENTION_DAYS` (30; 0 keeps everything) are pruned by the dispatcher once an
  hour, 500 rows a batch until none are left or two seconds have passed (`notify.prune_once`,
  `deliveries.prune`), so the prune keeps up with any delivery rate. Queued rows (`pending`, `sending`) are never
  pruned, so the Health page's queue counts and a restart's recovery are unaffected; the delivery
  list simply ends where the retention does.
- **Restart:** pending rows are in the database, so they resume. On start the dispatcher puts
  `sending` rows (claimed, never recorded: the process died mid-send) back to `pending`:
  at-least-once, receivers dedupe on event id. The fan-out cursor starts at the newest event, so an
  event committed in the instant between commit and fan-out of a crashing process is not delivered
  (narrow; the events table still has it for `GET /events`).
- **Concurrency:** up to `SLATE_WEBHOOK_CONCURRENCY` (8) attempts in flight; the loop wakes on every
  bus message and otherwise every second (retries come due on that poll). Ordering per subscription
  is not guaranteed once retries happen.
- **Disabled** subscriptions get no new rows; queued rows for a hook disabled before sending become
  `skipped` (re-enabling does not resend them).
- **`POST /webhooks/{id}/test`** records a `webhook.test` event (actor = caller), creates an
  already-claimed delivery and sends it inside the request, once, no retries; returns the attempt
  row. 422 if the hook is disabled.
- **Guard:** `Delivery` rows are `core.BOOKKEEPING_MODELS`, written without events. Subscription
  changes are ordinary events.

## Verifying a signature (copy for receivers)

Check `X-Slate-Signature-V2`: compute HMAC-SHA256 over the `X-Slate-Timestamp` value, a `.`,
and the **raw request bytes** (before any JSON parsing), keyed by the `whsec_...` secret as UTF-8;
compare in constant time, and reject a timestamp more than five minutes from your clock. The
older `X-Slate-Signature` (HMAC of the raw body alone) is still sent, but it cannot tell a
replayed delivery from a new one.

Python:

```python
import hashlib, hmac, time


def verify(secret: str, raw_body: bytes, timestamp: str, header: str) -> bool:
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 300:
        return False
    signed = timestamp.encode() + b"." + raw_body
    expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


# verify(secret, body, headers["X-Slate-Timestamp"], headers["X-Slate-Signature-V2"])
```

Node (Express; use the raw body, not `express.json()`):

```js
const crypto = require("crypto");

function verify(secret, rawBody, timestamp, header) {
  if (!/^\d+$/.test(timestamp || "") || Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) {
    return false;
  }
  const signed = Buffer.concat([Buffer.from(timestamp + "."), rawBody]);
  const expected = "sha256=" + crypto.createHmac("sha256", secret).update(signed).digest("hex");
  const a = Buffer.from(expected), b = Buffer.from(header || "");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

app.post("/slate", express.raw({ type: "application/json" }), (req, res) => {
  const secret = process.env.SLATE_WEBHOOK_SECRET;
  if (!verify(secret, req.body, req.get("X-Slate-Timestamp"), req.get("X-Slate-Signature-V2"))) {
    return res.sendStatus(401);
  }
  const event = JSON.parse(req.body);
  res.sendStatus(204); // answer fast; do the work afterwards
});
```

## Due events

`service/due.py`, run each scheduler tick (`SLATE_SCHEDULER_INTERVAL_SECONDS`, 60) through
`notify.due_events_tick`, the second entry in `scheduler.hooks` (after recurrence spawning).

- Stages per item, in time order: `item.due_soon` at `due_at − L` for each distinct lead `L` among
  active subscriptions that want `item.due_soon` (just 24 when there are none), `item.due` at
  `due_at`, `item.overdue` at `due_at + 1 h`.
- `items.last_due_event` stores the last stage key emitted (`soon:<h>`, `due`, `overdue`). A tick
  emits every stage whose time has come and that lies after the stored one, oldest first, in one
  `apply_change()` per item. Repeating a tick or restarting emits nothing new; a missed hour emits
  what was missed. `due_soon` is dropped once `due_at` has passed (a late start sends `due` and
  `overdue`, not "due soon").
- **Due date moved later** so the stored stage now lies in the future: that item starts over for the
  new date (it gets `due_soon`/`due`/`overdue` again): each stage fires once per due date, not
  once per item.
- Nothing for archived items, items in archived lists or sections, items in a `done` or `cancelled`
  status, or items with no due date. Completing before `+1 h` means no `overdue`.
- Actor: agent account `slate` (created on first need, as its own actor, source `system`). Event
  `field = "due_at"`, `after = {"due_at", "lead_hours"}` for due_soon, `{"due_at"}` otherwise. The
  column is written with a Core UPDATE that keeps `updated_at`, so a due event does not look like an
  edit.
- Delivery matches `item.due_soon` to a subscription only when `after.lead_hours` equals its
  `due_lead_hours`.

## Scheduler

Two background tasks, two cadences. `scheduler.py` ticks its `hooks` every
`SLATE_SCHEDULER_INTERVAL_SECONDS` (60), each hook with its own session; a failing hook is logged
and the rest run (`docs/recurrence.md` has the loop and the full hook list). Recurrence spawning
runs first and `notify.due_events_tick` second, so a just-spawned instance gets its due events in
the same tick. The webhook dispatcher is not a hook (it needs ~1 s latency and wakes on the bus):
`lifespan` starts it as its own task after the scheduler and cancels it before. Both run only when
`SLATE_SCHEDULER_ENABLED` (tests: off); the dispatcher also needs a valid `SLATE_SECRET_KEY`.

## Testing

- Receiver: `monkeypatch.setattr(notify, "transport", httpx.MockTransport(handler))`.
- Clock: `notify.Dispatcher(clock=fake)`; drive `start()`, `fan_out_once()`, `send_due_once()` by
  hand to check the backoff to the second. `due.emit_due_events(session, now=...)` takes the time
  directly.
- The one real-loop test uses a temp-file database (worker threads need their own connections).
