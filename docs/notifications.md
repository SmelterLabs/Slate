# Reminders, the daily digest and push notifications

Reminders and before-due alerts, the daily digest, Web Push (VAPID key, the push engine, the
`chat` reason and quiet delivery) and the push delivery receipts. The inbox itself is in
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
  and waiting on you. Open questions appear first, oldest first, with their text and age.
  Nothing on an empty day; an account found empty is checked again at most
  once an hour that day (`QUIET_RECHECK`), not every tick. Idempotent by the `date` in the last digest event. `GET /me/digest`
  previews it.
- **Where they land**: inbox reasons `reminder` and `digest` (`inbox.reason_for`, checked before the
  "own events are not news" rule), check-in `why` `reminder`/`digest` (also in the check-in SQL
  prefilter), webhooks with `assignee_me` (`deliveries.matches`), and push.
- **Web Push** derives its VAPID key from `SLATE_SECRET_KEY` and follows the event log
  from the `push` cursor. It uses the same recipient reason as the inbox, applies
  `push_reasons` and checks access again on every attempt. Its payload contains
  `title`, `body`, `url`, `tag`, `loudness` and `silent`.
  The endpoint allowlist and outbound address checks also apply on send. A device that
  returns 404 or 410, or reaches ten consecutive failures, records `push.expired`.
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

## Notification level

`service/attention.py` assigns the inbox entry's `loudness`.

| Reason | Person | Agent or Slate |
|---|---:|---:|
| Waiting, reminder, health | 3 | 3 |
| Mention | 3 | 2 |
| Chat | 3 | 1 |
| Reply, comment, assigned | 2 | 1 |
| Due, failed, stalled, unblocked, nudge, access | 2 | 2 |
| Digest | 1 | 1 |
| Watching, status, update and other reasons | 0 | 0 |

An urgent card adds one, capped at 4. Level 0 stays in the inbox. Push levels 1 and 2
are silent, and levels 3 and 4 have sound. Email requires at least level 3 and five minutes
unseen. The alert bridge uses level 2 or higher and sends level 4 to the phone.
Per-channel reason filters still apply at every level.

When a person receives entries from an agent's leased run, the card's alerts wait
until the lease ends. The loudest eligible entry is sent, and a question bypasses the hold.
Quiet hours (`quiet_hours` with `from` and `to` in `HH:MM`) use the workspace timezone. Level 4
passes through. Lower levels are held for one summary at the end, loudest entries first. The inbox still
shows each entry at its original level. Ordinary messages stay in Chat by default and can
also be read with `GET /inbox?reason=chat`.

`GET /inbox/alerts` starts at the latest release with an empty list and the `X-Slate-Cursor`
header. Pass that opaque cursor as `after` to get later releases, oldest first. Each release
has the same wording as the push, with its level, reason and source inbox entry. Quiet-hours
summaries represent multiple entries and have no item or source entry. The route rechecks access.
Push, email and the alert bridge apply their own delivery filters to these releases.

The scheduler records a recipient-only `inbox.escalated` event once per original entry.
An open question or an unread message or mention by a person reaches level 4 after
`escalate_after_minutes` (120 by default, 0 to disable). The escalation keeps its original
reason and begins "Still waiting: ". A due card with no person assignee also reaches its
person creator or the workspace owner.

## Push delivery receipts

`push_deliveries` holds one receipt per subscription/event/kind:
five attempts maximum in a 24-hour window, 30-second
exponential backoff capped at one hour, and Retry-After seconds/HTTP dates capped at six hours.
Each drain attempts at most 100 devices and expires at most 100 receipts. New subscriptions
never receive earlier events. The attention stream starts at the newest event on first activation,
and events older than a day are skipped. Retries survive restart, recheck account, access, preferences and subscription ownership,
and render task-reference titles with the recipient's read filter. Receipts are bookkeeping
(`core.BOOKKEEPING_MODELS`): no event, no payload, no keys. Attention releases precede receipt
creation. Each attempt commits once right after its send, and
expiry is one commit per drain. Successful receipts are not
retried; a process death *after provider acceptance but before its SQLite outcome commits* can
still duplicate that one device (Web Push has no transactional acknowledgement). No claim of
exactly-once delivery or actual OS toast display. See `tests/test_collaboration_attention.py`
and `tests/test_push_retry.py`.

- **Wording of an agent's ask and a failed run** (`push.notification_for`): a `waiting`
  entry on a card with an open ask (`comments.asks_of`) has the question as its title and
  "<Agent> asks on: <title>" as its body; any other `waiting` entry reads "<Agent> is waiting on
  you". A `failed` entry (`item.run_failed`, sent once when Slate gives up a run after three
  failures in a row) reads "<Agent> could not run: <title>" with the last failure's first line.
  A failed run before that, and the take that retries it, push nothing.

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
  address) and `email_reasons` (same reason words as push, default mention and waiting),
  validated in `accounts._validate_prefs`. `email.process()` queues `email_deliveries` (account,
  event) for released alerts whose reason matches and whose level is at least 3. Delivery is due
  once the alert is released and the event is at least five minutes old. The
  account still has not seen it (`email.seen`: inbox `read_through`/mark, `item_reads.read_at`
  after the event, or the chat message read or exposed); every due row for one account goes as
  ONE email (subject is the single title, or "N things in Slate need you"). Access and prefs are
  rechecked at send time and text is `push.notification_for` under the recipient's read filter.
  Failed sends retry every 5 min up to 4 times within a day. Receipts are bookkeeping (no
  events). `slate/emailer.py` runs it when `SLATE_SMTP_HOST` and `SLATE_SMTP_FROM` are set
  (`SLATE_PUSH_ENABLED` is Web Push only); the Health page tracks it as "Email notifications" with a cursor.
  `GET /me` says `email: true` so the board shows the column; `POST /email/test` sends a test.
  `SLATE_PUBLIC_URL` makes the links absolute. Agents (kind `agent`) are never emailed.
