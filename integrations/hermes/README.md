# Hermes adapters

Optional `--exec` commands for the generic Slate runner (`docs/agents/runner.md`). The server
knows nothing about Hermes. Deleting `integrations/` does not break `tests/`.

## Capability contract

These commands print one JSON capability declaration and exit without reading stdin,
credentials or profile settings, contacting either service, or starting an agent:

```sh
python integrations/hermes/run_task.py --capabilities
python integrations/hermes/run_task_cli.py --capabilities
```

| Adapter | `work_kinds` | `resume` | `approvals` | `remote_stop` |
|---|---|---|---|---|
| `hermes-gateway` | `task`, `chat`, `doc` | `session` | `native-ui-only` | `true` (card turns) |
| `hermes-cli` | `task` | `fresh-process` | `native-ui-only` | `false` |

The declaration also includes `version: 1`. It is an offline declaration, not a live readiness
probe or server-negotiated capability. Unsupported chat and work without an item ID return
`release` before any task lookup or runtime launch. The CLI does not pretend chat is a task.

## Gateway setup

Use one runner per machine, with an independent Slate account/token and stable writable
`HERMES_STATE` for each configured agent. A multiplexing gateway serves the default profile
at `http://127.0.0.1:8642` and another profile at `/p/<profile>`.

```toml
url = "https://slate.example.com"
exec = "python integrations/hermes/run_task.py"

[[agent]]
name = "my-agent"
token_file = "my-agent.token"
env = { HERMES_API_URL = "http://127.0.0.1:8642/p/my-agent", HERMES_API_KEY = "${API_SERVER_KEY}", HERMES_STATE = "my-agent-controls.json" }
```

Configure credentials through the runner's supported secure environment/token mechanism;
never commit them. A restrictive Slate token does not restrict a runtime's tools, memory or
credentials: the selected profile must already be appropriately scoped.

Settings:

- `HERMES_API_URL`, `HERMES_API_KEY`: gateway and optional bearer key.
- `HERMES_ASK`: passed through to the brief when set. It does not choose who a card waits on.
- `HERMES_STATE`: base path, default `~/.slate-hermes-approvals.json`.
- `HERMES_TURN_TIMEOUT`: how long a turn may go without an event from the runtime (a tool
  starting, text, a result), default 3600 seconds. It is not a limit on the turn's length: a
  turn that keeps working is watched until it ends. Keepalives are not events. Each follow-up
  turn (a late message the run did not read) gets the whole budget again. Not proof of remote stop.
- `HERMES_DOWN_GRACE`: gateway-down grace, default 20 seconds.
- `HERMES_NOTE_EVERY`: progress interval, default 60 seconds.
- `HERMES_ACTIVITY_EVERY`: on a card turn, each tool the agent starts (with its preview), its
  thinking and its commentary go to the task window's live box, at most one request per this
  many seconds (default 1, minimum 0.2). These lines are not history.
- `HERMES_SESSION_LINK`: default **0**, preserving a portable `thread.ref` without a URL.
  Set `1` only when Slate explicitly permits `hermes` links. `HERMES_PROFILE`, otherwise the
  gateway URL's `/p/<profile>`, otherwise `default`, determines the optional Desktop link.

New sessions send only a title. There is no per-card model override and no model-setting
mutation. New sessions use the configured runtime default; reused sessions keep their
existing model/provider settings.

## Card conversation and truthful outcomes

1. Fetch and validate the authorized `GET /items/{id}/brief` on **every** turn. Any HTTP
   failure, malformed response or empty text finishes `failed` before runtime work. No title-only fallback.
2. Reuse the saved session when it exists. A 404 can create a new session; an authorization
   error cannot. A create the gateway refuses because the card's own session title is already
   held (a previous create that timed out on the runner side still made the session) adopts
   that session and sends it the brief; a title held for another card is `failed`. Emit
   `thread.ref` promptly, retaining it on runtime failure and brief errors
   when a previous ref is known.
3. Send the brief on a first turn, or the card reply as quoted data with stable author ID,
   kind, role, handle, comment ID and timestamp. Unknown provenance stays unknown; a person's
   comment is not automatically an owner/admin approval. Ordinary scoped questions still work.
4. Stream with `author: {id: "bot:slate", name: "Slate", is_bot: true}`. Only a confirmed
   `run.completed` permits the closing line or trailing ask lines to decide the task outcome:
   `SLATE: done: <status>`, `SLATE: review: <status>`, `SLATE: later: <status>`,
   `SLATE: answered: <status>`, or `SLATE: ask <handle>: <question>`. The status is one
   line of where the card stands. Review is for inspecting finished work and requires a
   status. A bare review is `done`. Use ask for each decision a person must make, even
   after the work is finished. Put one question per trailing line, up to four for the
   same person. The adapter sends `questions` for several and `question` for one.
   `assistant.completed`, a transcript closing line and transport `done`
   do not prove execution success. Failure, cancellation, interruption, incomplete status,
   approval and step/budget limits finish `failed` (the machine text is `note`) and wait on
   nobody. An answer with no closing line is `answered`. An old `SLATE: blocked waiting_on`
   line is not recognized, so that turn is `answered` too. A step or budget limit is `failed`
   with the limit in `note`; Run now continues in the same session.
5. The generic runner writes the final reply/outcome to Slate and settles only the take's
   dispatched Asks. Adapters never bulk-resolve Asks or write task status directly.

## While a card turn runs: new messages and Stop

The gateway adapter watches the runner's control file (`SLATE_CONTROL`, see
`docs/agents/runner.md`) once a second while a card turn runs.

- **A new message** is steered into the live run (`POST /v1/runs/{run_id}/steer`), quoted with
  its provenance like any card reply; the agent reads it with its next tool result and carries
  on. The adapter then prints `{"seen": [id]}`. A message the gateway would not take (the run
  was ending), or that the run ended without reading (`pending_steer` on the terminal event),
  is sent as one more turn in the same session before the result is reported, so it is never
  dropped and never answered by a reply that did not read it. When the agent had already
  answered (a steer that lands as it finishes is appended and the run goes on to a second
  answer), the card's reply is the first answer followed by the second, not the second alone.
  The run's outcome is the last answer's closing line; when that answer has none (the agent
  closed the task in the first one and only added to it), the first answer's line stands.
  An answer with no closing line is not asked for one.
- **Stop** is `POST /v1/runs/{run_id}/stop`, retried each second until the gateway takes it.
  The cancelled turn is reported `answered`, waiting on nobody, with the session kept: a reply
  continues in it. Stop wins over a message still waiting to be steered.

Card text still authorizes nothing: a steered message is quoted data, exactly as a reply is.
Chat and doc turns, and the CLI adapter, have no mid-run control.

After stream loss, message polling can detect a missing/denied session, gateway restart or
outage. It **cannot** convert transcript text to success. An observation timeout is `failed`
and names nobody; it does not claim the remote turn stopped.

## Approvals and reconciliation: fail closed

Natural-language card/chat replies never settle a runtime control. There is no keyword
parser, guessed approval endpoint, or “owner approved; run it now” fallback.

On `approval.request`, progress points to the **native Hermes approval UI** and the adapter
keeps the turn's SSE stream open. Disconnecting this native endpoint interrupts its executor;
an approval is not a detach point. The generic runner continues heartbeating its held work.
Pending state retains the session, exact request ID when supplied (`prompt_id`, `request_id`,
`id` or `approval_id`), execution generation, run ID, command digest, runtime/profile and item scope.
Unknown fields are not invented. Control identity is saved atomically as soon as observed,
before waiting for the native decision. Progress does not echo command text. If observation
ends without a terminal result, the identity stays pending across subsequent replies and
session replacement attempts. The native UI authenticates the approving person.

Current authoritative API reference:
<https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server>.
Its native control contract requires exact request/generation identity. This adapter has no
verified Slate-person-to-native-owner control transaction and **does not implement automatic
approval or detached approval-resolution readback**. A native click alone does not clear local
state; an authoritative terminal event on the still-observed turn retires its controls.

Before remote work, the adapter atomically persists an observation marker. Confirmed normal
return clears that marker; an approval replaces it with pending control identity. A crash,
unconfirmed remote outcome, or failed approval-state write leaves a durable blocker, so a
later invocation does not blindly replay the turn. Confirmed terminal failures still report
`failed` (chat and doc: `release`), but retire that turn's observation/control state so an
explicit later retry can reuse the session. Successful completion permits the normal
closing-marker contract. A late message gets a fresh observation fence before submission.
Corrupt or unreadable state is `failed`.

Health/history preflight errors are definitely unsubmitted and clear the fence, permitting
an explicit retry after recovery. Native pre-execution HTTP refusals (400/401/403/404/409)
likewise permit an explicit retry, not an automatic resubmit. HTTP 422 has no verified
pre-admission path in the native handler, so it remains conservatively
unconfirmed, like 5xx and post-submit transport loss. No transcript text, timeout, or local
stream close is proof of remote termination. Observation budgets include native keepalives.

When the stream is lost or goes silent after the runtime named the turn's run (`run_id` on
its events), the adapter reads that run's own status (`GET /v1/runs/{run_id}`) before giving
up: a completed run's `output` is the turn's answer, a failed or cancelled one is a confirmed
failure, and a run still going (or a gateway that cannot say) stays unconfirmed.

Source basis: Hermes Agent's `gateway/platforms/api_server.py`: `_prepare_session_chat`,
`_handle_session_chat_stream`, `_register_session_stream_approval`, and
`_drain_session_stream_task_on_disconnect`. Offline controlled-generator tests assert that
iteration continues after approval, persistence precedes submission/waiting, and stream close
occurs only after terminal completion or an honestly unconfirmed observation end.

**Operator recovery:** stop scheduling that adapter's affected work, independently verify the
exact native session/request/generation and terminal status (and that no remote work remains),
then reconcile the matching state entry. Do not clear state just to bypass a pending control
or “retry” unknown work. No automatic reconciliation command is shipped; live readback and
verified owner identity remain prerequisites for adding one.

One case settles itself, on runtime evidence only: an `unconfirmed_turn` marker (a turn that
outlived its watch, or a wrapper killed mid-turn, say when the runner lost its lease) is
cleared at the next run when the gateway proves that turn is over. The marker saves the
gateway's life (`gateway`: start time, else `/health/detailed` pid) and, once the runtime
names it, the turn's `run_id`. The turn is over when the gateway restarted since; when the
marker names a run and `GET /v1/runs/{run_id}` says it ended, or answers `run_not_found` (a
gateway forgets a run only some time after it ended); or, for a marker with no run or a
gateway without that route, when `/health/detailed` reports `active_api_runs == 0`. While the
marker's own run is still going, nothing else clears it. Otherwise, or when the gateway
cannot say, the marker keeps blocking. Chat markers settle the same way; pending approvals
are unchanged.

The answer of a card turn that ended unwatched is not lost. When the gateway still holds the
run's `output`, a Run now with no new message reports that answer as the run's result without
asking the agent anything, and a run started by a new message puts it ahead of the new
answer. When the gateway no longer holds it, the next turn's prompt tells the agent its last
answer never reached the card and asks it to repeat what still matters. A session transcript
is never read for this. A chat turn's unwatched answer is not posted.

State layout:

- `<HERMES_STATE>.d/<sha256(item_id)>.json`: independent atomic card records. A whole-map file
  at the base path, when one exists, is a read-only fallback; a `null` sidecar is a tombstone
  that overrides it.
- `<HERMES_STATE>.chat-sessions/<sha256(scope)>.json`: session ID plus observation/pending
  control identity when necessary. No chat bodies, credentials or command text are stored.
- Back up all three. Keep each agent's state base separate. Drain an adapter's work before
  updating it.

## Section and agent chat

The gateway adapter handles chat turns, including `section:…` and `agent:…` channels. Stdin's
`item: null`, `chat` coordinates take precedence over stale task environment variables. A real
item continues to select the card path. The authenticated `/me` account must match the chat
lease before any turn starts. Scope keys include Slate URL, gateway/profile, agent account,
channel and thread, so distinct channels/threads/agents never share sessions accidentally.

Resolve message authors through read-scoped `/agents?kind=all`; preserve roster IDs, kinds
and roles, never claimed message metadata. Person collaborators can ask bounded project
questions; history is quoted. `HERMES_CHAT_OWNER` (stable account ID preferred, exact handle
supported) or `/me` `prefs.owner` is an operator trust pin, not proof of workspace role or
runtime approval. Missing identity never promotes a sender. Do not pin a guest or a reusable
handle. These prompt rules are not a sandbox.

A nonempty answer with `SLATE: done` **and runtime success** yields `done`/`reply`; the generic
runner posts it to the supplied channel/thread. All failure/approval/unconfirmed paths yield
`release` with no publishable reply. No task brief, card nudges or task mutation occurs.
Progress is bounded and does not disclose commands. Pending chat approvals block later
mentions until independently reconciled in the native runtime; an unconfirmed chat turn
blocks them until the gateway proves it over (above).

## CLI adapter

`run_task_cli.py` runs a fresh command per card turn:

```sh
$HERMES_CLI --profile $HERMES_PROFILE chat --quiet --max-turns $HERMES_MAX_TURNS --query '<query>'
```

`HERMES_CLI` defaults to `hermes`; profile is omitted when unset. `HERMES_HOME` is removed
from the child environment so profile selection remains native. Defaults: 50 maximum turns,
600-second command timeout. Each reply is quoted with provenance, followed by the brief again.
A zero process exit plus a closing marker permits that outcome; nonzero exit, launch failure
or timeout is `failed` with the machine text as `note`. No marker is `answered`. Process
termination is not proof that spawned
or remote work stopped. No remote cancellation or cross-turn session reference is supported.
Keep `run_task.py` beside the CLI adapter for its parser.

## Offline tests

```sh
uv run ruff check .
uv run pytest -q integrations/hermes integrations/runner-example
uv run pytest -q
```

Tests use mock HTTP transports, fake CLI processes and disposable state/DBs. They cover
approval identity/replay/write failures, provenance, denied briefs, runtime outcomes, scoped
chat reuse, capability output and model-setting preservation. They do not prove live control
approval or native cancellation against a real gateway. Before updating the adapter a runner
uses, drain that runner's work, and keep the adapter, runner and server on matching versions.
