# State machines

Everything in Slate that moves through a fixed set of states, in one place: the states, the one
rule that reads the state, what moves it, and where it is shown. Read this before adding a
state, a transition, or a second place that works a state out for itself.

Two rules hold for all of them:

- **A state is read by one function.** Nothing else compares the columns underneath
  (`until`, `claimed_at`, `quarantined`, a `*_at` timestamp) to decide what state a row is in.
- **The words are declared once.** `tests/test_state_vocabularies.py` fails when the API accepts
  or returns a word the declaration does not have, when a queue row is given a state outside its
  vocabulary, or when this file does not name a declared state.

None of these is a board column (product rule 4): a column is a status the list's owner named.

## Agent badge (a card's flag)

Declared: `models.AgentState` (stored), `reports.STATES` (what a report may say).
Read: `reports.flag_of(item)`. Shown: the card's badge, `agent` on the item view.

| Report | Effect on the flag |
|---|---|
| `working`, `blocked`, `review` | sets it (who, when, note, progress, link) |
| `failed` | sets it: the run broke; nobody is told, and the work loop retries the card |
| `done`, `idle` | clears it; `done` also moves the card to its list's first done status |

A `blocked` report that waits on a person carries a structured ask of one to four questions.
Its note is the question headline. Waiting on an agent, or on nobody, needs no ask.

Every report is one `item.reported` event. Besides an agent's own report, the flag changes
when: a lease runs out (`working` becomes `blocked`, by `slate`); a person moves the card (a
`review` flag clears); anyone closes the card (a `review`, `blocked` or `failed` flag clears); a person
presses Clear or Mark reviewed (an `idle` report of their own). A `blocked` or `review` flag nobody refreshed for
`reports.CLAIM_FRESH` (6 hours) and that has no lease counts as abandoned: another agent may
claim the card. A `working` flag with no lease is cleared sooner (Stalled card, below).

## Work lease (an agent's hold on a task)

Declared: `work.LEASE_STATES`. Read: `work.lease_state(row, now)`. Shown: `state` on a lease
from `POST /work/next`, `GET /work` and `GET /agents/plan`; the Agents page's "holding" and
"timed out" lines.

| State | Means | Leaves by |
|---|---|---|
| (no row) | nobody holds the task | `POST /work/next` takes it: `working` |
| `working` | a run holds it; `until` is ahead | heartbeat (stays `working`); the holder's finish (row deleted; a plain report never ends a run that has a run proof); `until` passes: `expired`, or the row is deleted when the card is closed (done or cancelled) |
| `expired` | the lease ran out; the run may still be going, so the task stays held | the agent's reconcile with the run's proof, or a person's Clear on the badge (row deleted) |

A lease is `expired` from the moment `until` passes. The scheduler then marks the row
(`quarantined`) and turns a `working` badge `blocked`, within one scheduler tick. The mark
records that this happened; a person's Clear settles a lease only once it carries the mark. An
`expired` lease refuses heartbeat, finish and stop, and `/work/next` does not offer the task to
anyone. A lease that runs out on a closed card is not quarantined: the scheduler settles it
(`item.work_finished`, reconciled) and deletes the row, since a closed card is not run again.
The same happens to a lease that was quarantined while the card was open, once the card is
closed: the close took the badge off, so nobody could Clear it.

A finish names one of `work.OUTCOMES`: `done`, `review`, `later`, `ask`, `answered`,
`failed`, `blocked`, `release`.

| Outcome | Badge | Who the card waits on | Also |
|---|---|---|---|
| `done` | cleared | clears with completion | completes the card unless the agent took it only to answer a question |
| `review` | `review` | unchanged | needs a `status` (what to check); the person it is for is whose move it is |
| `later` | cleared | unchanged | needs a run time still to come |
| `ask` | `blocked`, note = the question headline | the person asked | the questions are one Ask to them |
| `answered` | cleared | unchanged | the turn's Asks resolve (`answered`); not offered again until a new Ask or Run now |
| `failed` | `failed`, note = what broke | unchanged | retried at 5 and 30 minutes; the third failure is `item.run_failed` |
| `blocked` | `blocked` | sets an agent if named; otherwise preserves the existing field | use `ask` to wait on a person |
| `release` | cleared | unchanged | un-assigns the agent |

A failed run is offered again only when its retry time has come (`work.RETRY_AFTER`, the
scheduler's `item.run_requested` with `reason: "retry"`) or someone presses Run now, which also
starts the count over (`work._failures`).

Every agent finish except `failed` and `release` resolves the questions the turn took or
read within its settlement boundary. Only agent reports other than `working` set the
card's standing line (`after.status`).

## Stalled card

Declared: `items.stalled_at`. Read: the item view's `stalled` (set and open). Shown: a
"Stalled" mark with Run now.

| State | Means | Leaves by |
|---|---|---|
| not stalled | normal | the agent's newest finish is `answered`, 4 hours pass with nothing else: Slate pokes it once (`item.run_requested`, `reason: "stalled"`); that run ends `answered` again: stalled (`item.stalled`) |
| stalled | an eligible card received a retry and the retry ended `answered` while the card still had no future schedule | Run now, a new question for an agent, parking, a future start or due date, closing the card (reopening it does not bring the mark back), or a finish other than `answered` |

A `working` badge with no lease and no report for 30 minutes is cleared by the scheduler
(an `idle` report, `reason: "stale"`), so an open card never shows a run that is not there.

## Chat and doc turns (an agent mentioned in a channel or on a page)

Declared: `turns.STATES`. Read: `turns.state(row, now)` for both tables (`chat_wakes`,
`doc_wakes`). Shown: `agent_turns[].state` on a channel, which the chat's agent line follows
(`web/src/lib/chat.ts` keeps it current from the turn events).

| State | Means | Leaves by |
|---|---|---|
| (no row) | nothing owed | a person @mentions the agent: `waiting` |
| `waiting` | mentioned, no runner has taken it | a take: `working`; the agent can no longer answer there: dropped (row deleted) |
| `working` | taken, lease live | heartbeat; finish (row deleted, or `waiting` again when a mention arrived meanwhile); lease runs out: `expired` |
| `expired` | taken, lease ran out | the agent's reconcile, a person's clear, or a new mention there (settles the dead run, then `waiting`) |

Each move is one `message.agent_turn` or `doc.agent_turn` event whose `after.state` says what
happened: `started`, `done`, `released`, `expired`, `dropped`. Those are transitions, not
states of the row. A turn's finish names one of `turns.OUTCOMES`: `done`, `release`.

## Push and email notifications

Declared: `models.SendState`, shared by `push_deliveries` and `email_deliveries`; the columns
refuse any other word. One row per event and device (push) or event and account (email).

| State | Means | Next |
|---|---|---|
| `pending` | waiting for its time (a retry keeps the row `pending` with a later time) | `sent`, `failed`, `dropped` |
| `sent` | delivered to the push service or the mail server | final |
| `failed` | out of attempts or past its window, or it could not be built | final |
| `dropped` | no longer wanted: already seen, access or preferences changed, the device is gone | final |

## Agent connection

Read: `connections.view(session, row)["state"]`, worked out from the pairing code, the
credential and when the runner was last seen: `waiting` (code not used yet), `connected`,
`ready` (its test passed), `offline` (not seen for two minutes), `problem` (the detail says
which), `revoked`. Nothing stores this word; it is always computed.

## A task's place

Read: an item is `trashed` when `trashed_at` is set, else `archived` when `archived_at` is
set, else `closed` when its status kind is `done` or `cancelled` (`lists.CLOSED_KINDS`), else
`open`. This is the order `existing_state` uses on a taken `external_ref`. Trashing also sets
`archived_at`, so every "hide archived" path hides trash; restore undoes only what the trash
set.

## Due stages

`items.last_due_event` holds the last stage emitted for the current due date, in time order:
`soon:<hours>` (each lead a subscription asked for), `run` (when the task has an agent
assignee), `due`, `overdue`. `due.pending_stages()` is the one rule: it returns the stages
whose time has come and that come after the stored one, and starts over when the due date
moves later.

## Asks and review threads

- An Ask (`comments.assignee_id` set) is open until `resolved_at` is set, and can be reopened.
  "Seen" is not a state of the Ask: it is a `comment.seen` event from a run that read it.
- A doc review thread is open until resolved; `accepted_at` records that its suggested
  replacement was applied, which also resolves it.

## The board's own

- **Live stream** (`web/src/lib/board/stream.svelte.ts`): `connecting`, `live`, `reconnecting`.
- **A self-saving text box** (the task description, `AutoText.svelte`; a page,
  `docs.svelte.ts`): `idle`, `pending` (typed, not sent), `saving`, `saved`, `error`, and for a
  page `conflict`. Text in `pending`, `saving` or `error` is never replaced by the server's.
- **A chat turn** follows the server's three states (`web/src/lib/chat.ts`).

## Inbox reasons

Not a state, but a vocabulary kept the same way: `inbox.REASONS` is every reason
`inbox.reason_for()` can give, and the API's `Reason` repeats it, so an entry is never one the
route cannot serve.

## Recurrence rules, invitations and tokens

- A rule is active, paused (`paused_at`) or ended (`ended_at`); ended is final.
- An invitation is pending until it is accepted, revoked or seven days old; each is final.
- A token works until it is revoked or expires; a disabled account's tokens do not work.

These three are read where they are used; each has one writer (`recurrences`, `collaboration`,
`accounts`).
