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
| `hermes-gateway` | `task`, `chat` | `session` | `native-ui-only` | `false` |
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
- `HERMES_ASK`: optional blocked-recipient override; otherwise use the authorized brief's
  structured `ask`, then `owner`. It is not an authority grant.
- `HERMES_STATE`: base path, default `~/.slate-hermes-approvals.json`.
- `HERMES_TURN_TIMEOUT`: observation timeout, default 3600 seconds; not proof of remote stop.
- `HERMES_DOWN_GRACE`: gateway-down grace, default 20 seconds.
- `HERMES_NOTE_EVERY`: progress interval, default 60 seconds.
- `HERMES_NUDGES`: at most two follow-up prompts by default after a confirmed successful
  runtime turn without a closing marker. No nudge follows an unconfirmed/failed turn.
- `HERMES_SESSION_LINK`: default **0**, preserving a portable `thread.ref` without a URL.
  Set `1` only when Slate explicitly permits `hermes` links. `HERMES_PROFILE`, otherwise the
  gateway URL's `/p/<profile>`, otherwise `default`, determines the optional Desktop link.

New sessions send only a title. There is no per-card model override and no model-setting
mutation. New sessions use the configured runtime default; reused sessions keep their
existing model/provider settings.

## Card conversation and truthful outcomes

1. Fetch and validate the authorized `GET /items/{id}/brief` on **every** turn. Any HTTP
   failure, malformed response or empty text blocks before runtime work. No title-only fallback.
2. Reuse the saved session when it exists. A 404 can create a new session; an authorization
   error cannot. Emit `thread.ref` promptly, retaining it on runtime failure and brief errors
   when a previous ref is known.
3. Send the brief on a first turn, or the card reply as quoted data with stable author ID,
   kind, role, handle, comment ID and timestamp. Unknown provenance stays unknown; a person's
   comment is not automatically an owner/admin approval. Ordinary scoped questions still work.
4. Stream with `author: {id: "bot:slate", name: "Slate", is_bot: true}`. Only a confirmed
   `run.completed` permits the final `SLATE: done | review | blocked waiting_on X` to decide
   the task outcome. `assistant.completed`, a transcript closing line and transport `done`
   do not prove execution success. Failure, cancellation, interruption, incomplete status,
   approval and step/budget limits outrank success text. Missing status means blocked.
5. The generic runner writes the final reply/outcome to Slate and settles only the take's
   dispatched Asks. Adapters never bulk-resolve Asks or write task status directly.

After stream loss, message polling can detect a missing/denied session, gateway restart or
outage. It **cannot** convert transcript text to success. An observation timeout blocks and
asks for native inspection; it does not claim the remote turn stopped.

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
blocked/release, but retire that turn's observation/control state so an explicit later retry
can reuse the session. Successful completion permits the normal closing-marker contract.
Each nudge gets a fresh observation fence before submission. Corrupt/unreadable state blocks.

Health/history preflight errors are definitely unsubmitted and clear the fence, permitting
an explicit retry after recovery. Native pre-execution HTTP refusals (400/401/403/404/409)
likewise permit an explicit retry, not an automatic resubmit. HTTP 422 has no verified
pre-admission path in the native handler, so it remains conservatively
unconfirmed, like 5xx and post-submit transport loss. No transcript text, timeout, or local
stream close is proof of remote termination. Observation budgets include native keepalives.

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

One case settles itself, on runtime evidence only: a card's `unconfirmed_turn` marker (a
wrapper killed mid-turn, say when the runner lost its lease) is cleared at the next run when
the gateway proves no turn can still be running: it restarted since the marker was written
(start time, else `/health/detailed` pid, saved as `gateway`), or `/health/detailed` reports
`active_api_runs == 0`. Otherwise, or when the gateway cannot say, the marker keeps blocking.
Pending approvals and chat markers are unchanged.

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
Progress is bounded and does not disclose commands. Pending/unconfirmed chat state blocks
later mentions until independently reconciled in the native runtime.

## CLI adapter

`run_task_cli.py` runs a fresh command per card turn:

```sh
$HERMES_CLI --profile $HERMES_PROFILE chat --quiet --max-turns $HERMES_MAX_TURNS --query '<query>'
```

`HERMES_CLI` defaults to `hermes`; profile is omitted when unset. `HERMES_HOME` is removed
from the child environment so profile selection remains native. Defaults: 50 maximum turns,
600-second command timeout. Each reply is quoted with provenance, followed by the brief again.
A zero process exit plus closing marker permits success; nonzero exit overrides any marker.
No marker, launch failure or timeout blocks. Process termination is not proof that spawned
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
