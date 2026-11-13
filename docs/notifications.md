# Reminders, the daily digest and push notifications

Reminders and before-due alerts, the daily digest, Web Push (VAPID key, the push engine, the
`chat` reason, re-nudges) and the push delivery receipts. The inbox itself is in
`docs/task-window.md` (Productivity).

## Reminders, daily digest and push notifications

- **Reminders** (`reminders` table): one account, one item, a fixed `remind_at` **or**
  `before_minutes` before the item's due date (0 = at it). `POST /items/{id}/reminders {at |
  before_minutes, account?, note?}` records `item.reminder_set`; `DELETE /reminders/{id}` (the
  reminded account or whoever set it) `item.reminder_removed`. The scheduler hook
  `notify.reminders_tick` → `reminders.fire_reminders()` records `item.reminder` as the `slate`
  system agent (`after = {reminder_id, account_id, account, note, at, due_at}`) for open items only.
  `fired_for` stores the time it fired for, so a lead-time reminder fires again when the due date
  moves later. `GET /reminders` lists your unfired ones.
- **Alerts before due**: any number per item, none by default. `add_reminder`
  with a `before_minutes` the same account already has on that item returns that row (no event), so
  the board's toggles and agent retries never duplicate. A date-only due date (`due_date_only`,
  stored as 17:00 workspace time) counts back from 17:00, the moment it turns overdue; not from the
  agents' 06:00 run time. `recurrences._spawn` copies the previous instance's before-due alerts onto
  each new instance (`reminders.copy_due_alerts`, one `item.reminder_set` each, always, not behind
  `copy_checklists`); fixed-time reminders are not copied. Reopen and roll-forward keep the same
  item, so its alerts re-arm because the due date moved.
- **Daily digest**: hook `notify.digest_tick` → `send_digests()`. Once per day, at the account's
  prefs `digest_hour` in the workspace timezone (default 7; prefs `digest` defaults on for people,
  off for agents) it records `account.digest` with what is overdue and due today (assigned to you)
  and waiting on you. Nothing on an empty day; an account found empty is checked again at most
  once an hour that day (`QUIET_RECHECK`), not every tick. Idempotent by the `date` in the last digest event. `GET /me/digest`
  previews it.
- **Where they land**: inbox reasons `reminder` and `digest` (`inbox.reason_for`, checked before the
  "own events are not news" rule), check-in `why` `reminder`/`digest` (also in the check-in SQL
  prefilter), webhooks with `assignee_me` (`deliveries.matches`), and push.
- **Web Push**: `webpush.py` with only `cryptography`. The VAPID P-256 key is derived from
  `SLATE_SECRET_KEY` (HMAC "slate-vapid"), so nothing new to configure or back up; rotating the
  secret key changes it and the board re-subscribes on its next load (`lib/push.ts syncPush`).
  The VAPID contact (`webpush.subject`) is `SLATE_PUSH_SUBJECT`, else an https `SLATE_PUBLIC_URL`;
  `webpush.SUBJECT` is only the placeholder used when neither is set, so set one of them.
  Subscriptions (`push_subscriptions`) hold the endpoint and the browser's `p256dh`/`auth` (in
  `core.SECRET_COLUMNS`, never in events); `last_ok_at` and `failures` are bookkeeping.
  `push.process()` follows the event log from the `engine_cursors` row `push` (like automations),
  asks `inbox.reason_for` per subscribed account, filters by prefs `push_reasons` (default mention,
  reply, chat, assigned, waiting, due, unblocked, nudge, reminder, digest, health), and POSTs one
  encrypted JSON `{title, body, url, tag}` per device (tag = item id, so a busy task replaces its
  notification) through `outbound.post`, like webhooks: besides the `SLATE_PUSH_ENDPOINT_HOSTS`
  allowlist (even `["*"]`), the endpoint must resolve to public addresses unless
  `SLATE_WEBHOOK_PRIVATE_TARGETS` is on (checked at subscribe and at every send; a refused
  endpoint is a final failure), the connection goes to the checked address, and the answer is
  drained up to 64 KB within one deadline. 404/410 or 10 failures in a row removes the subscription (`push.expired`, actor =
  its owner, source system). `slate/pusher.py` runs it when `SLATE_PUSH_ENABLED` (default on; off in
  pytest) and a secret key is set. **`chat`** is the one push-only reason:
  `push.chat_reason` turns any `message.posted` by someone else that `inbox.reason_for` did not
  already claim (mention, reply) into a `chat` push, so a plain message in a channel reaches the
  desktop as a toast; who can read the channel is the same `EventGate` as the stream. It is
  deliberately not an Inbox entry (the Inbox would fill with channel traffic; the channel's unread
  badge is its record), so `inbox.reason_for` does not know it. Its tag is `chat:<channel>` (a busy
  channel replaces its toast) where mentions and replies keep `chat:<thread>`. Rejected: a
  per-channel follow toggle. **Re-nudge:**
  `push.renudge(session, send, nudged, now=, seed=)` runs once a minute from `pusher.step()`: for
  each subscribed account with `chat` on and prefs `chat_renudge_minutes` > 0 (default 10, max 1440,
  validated in `accounts._validate_prefs`), each readable channel (`access.compute` under
  `unrestricted`) whose newest unread message is older than that gets one push (`Still unread:
  #Todo`, same `chat:<channel>` tag, `renudge: true`). `nudged` is a process-level dict `(account,
  channel) -> newest message id`; the first step after a start runs with `seed=True`, marking what
  is already eligible without sending, so a restart never replays nudges; a newest message older
  than `RENUDGE_WINDOW` (a day) is marked and skipped. In memory on purpose (rule 8): at worst one
  nudge is lost across a restart. Rejected: a `push_nudges` table (a migration for a reminder of a
  reminder).
- **REST**: `/items/{id}/reminders`, `/reminders`, `/reminders/{id}`, `/me/digest`, `/push/key`,
  `/push/subscriptions` (GET, POST, DELETE `/{id}`), `/push/unsubscribe {endpoint}`, `/push/test`.
  **MCP**: `slate_reminder` (add, list, upcoming, remove, digest).
- **Board**: `components/task/Reminders.svelte` (chips + "Remind me" menu: "Before due" toggles
  `BEFORE_DUE` (at due, 5 min … 1 week) plus a custom amount (`customLead`), which keep the menu
  open, then the fixed-time `presets` from `lib/reminders.ts`; live on `item.reminder*` events),
  `components/NotificationSettings.svelte` (bell in the sidebar footer and the palette; state in
  `lib/notify.svelte.ts`), `lib/push.ts` (subscribe, unsubscribe, `syncPush` on start).
  `public/sw.js` shows the notification and, on tap, focuses an open window and posts `{type:
  'slate-open', url}` (App routes it) or opens one. iPhone gets push only in the installed app (iOS
  16.4+).
- **Testing push**: `tests/test_push.py` subscribes a fake browser (its own P-256 key) behind an
  `httpx.MockTransport` on `webpush.transport`, decrypts what was sent with `webpush.decrypt`, and
  checks the VAPID JWT signature. Headless Chromium cannot subscribe (no push service), so the e2e
  covers the settings and reminders, not delivery.

## Push delivery receipts

`push_deliveries` (migration `0f6da629237e`) holds one receipt per subscription/event/kind:
atomic enqueue plus cursor advancement, five attempts maximum in a 24-hour window, 30-second
exponential backoff capped at one hour, and Retry-After seconds/HTTP dates capped at six hours.
Each drain attempts at most 100 devices and expires at most 100 receipts. New subscriptions
never receive earlier events; first activation seeds the cursor, and events older than a day
are skipped. Normal and renudge retries survive restart, recheck account/access/preferences/
subscription ownership, and render task-reference titles with the recipient's read filter.
Renudge retries also require the same newest message still unread. Receipts are bookkeeping
(`core.BOOKKEEPING_MODELS`): no event, no payload, no keys; enqueue commits
once per page of events with the cursor, each attempt commits once right after its send, and
expiry is one commit per drain. Successful receipts are not
retried; a process death *after provider acceptance but before its SQLite outcome commits* can
still duplicate that one device (Web Push has no transactional acknowledgement). No claim of
exactly-once delivery or actual OS toast display. See `tests/test_collaboration_attention.py`
and `tests/test_push_retry.py`.

## Email notifications and the waiting bell

- **Why**: an easy way to see "something needs me" without opening a view that
  has the card, and a ClickUp-style "how and for what" without a wall of options. SMS is out of
  scope.
- **The bell** (`web/src/components/WaitingBell.svelte`, `lib/waiting.ts`): top right of the
  favorites bar on a desktop, left of Agents (phones keep the top bar's bell). Chips: `@N` for
  unread mentions/Asks, a chat bubble with N for messages (unread replies and comments in the
  Inbox plus plain chat still unread; a chat mention counts once, as a tag, because its
  channel's `mentions` are taken out of `unread`). Its menu lists the unread Inbox groups (open
  like the Inbox does) and chats with plain unread, with Open Inbox, Mark all read and
  Notification settings. No new endpoint: it reads `inbox` and `chat` state, already live.
- **Tell me when** (`NotificationSettings.svelte`): seven rows (`lib/push.ts NOTIFY_GROUPS`)
  against Toast / push and Email, rather than one checkbox per reason. A row stands for several
  server reasons; its box is checked when all are chosen, indeterminate when some are, and ticking
  it sets them all. The server stores the reasons themselves (`push_reasons` is the stored truth).
- **Email** (`service/email.py`, `slate/emailer.py`, `slate/mailer.py`): prefs `email_to` (one
  address) and `email_reasons` (the push reasons minus `chat`; default mention and waiting),
  validated in `accounts._validate_prefs`. `email.process()` follows the event log from the
  `engine_cursors` row `email`, queues `email_deliveries` (account, event) for people whose
  chosen reasons match, due `UNSEEN_DELAY` (5 min) later. When due, a row is sent only if the
  account still has not seen it (`email.seen`: inbox `read_through`/mark, `item_reads.read_at`
  after the event, or the chat message read or exposed); every due row for one account goes as
  ONE email (subject is the single title, or "N things in Slate need you"). Access and prefs are
  rechecked at send time and text is `push.notification_for` under the recipient's read filter.
  Failed sends retry every 5 min up to 4 times within a day. Receipts are bookkeeping (no
  events). `slate/emailer.py` runs it when `SLATE_SMTP_HOST` and `SLATE_SMTP_FROM` are set
  (and `SLATE_PUSH_ENABLED`); the Health page tracks it as "Email notifications" with a cursor.
  `GET /me` says `email: true` so the board shows the column; `POST /email/test` sends a test.
  `SLATE_PUBLIC_URL` makes the links absolute. Agents (kind `agent`) are never emailed.
