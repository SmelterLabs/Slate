# The runner: let Slate start your agent

`clients/python/slate_runner.py` runs an agent's tasks by itself. It holds the agent's live
stream, and whenever a task is assigned, comes due or someone presses **Run now** on the card,
it takes that task and runs a command you choose. The command is the only agent-specific part:
Slate never launches anything itself (it stays the doorbell), and the runner never knows what
kind of agent is on the other end.

Install it on the machine where the agent runs (it needs only `httpx`):

```
uv tool install "git+https://github.com/SmelterLabs/Slate#subdirectory=clients/python"
slate-runner --url https://slate.example.com --token-env SLATE_TOKEN \
    --exec "python run_one.py" [--parallel 1] [--lease 900] [--state ~/.slate-runner.json]
```

`python slate_runner.py ...` from a copy of `clients/python/` works the same.

**Keep it in step with the server.** The runner sends `X-Slate-Runner: slate-runner/<protocol>`
on every call. When the server needs a newer protocol it answers **426** with how to update;
the runner logs `OUT OF DATE: ...` and retries every 5 minutes. Upgrade with
`uv tool upgrade slate-runner` (or copy the files from the server's commit) after each server
upgrade. The server's health check (`healthz`) reports `min_runner_protocol`.

| Option | Meaning |
|---|---|
| `--url` | Slate's address, without `/api/v1` |
| `--token-env` | Name of the env var that holds the agent's token (`write` scope). Default `SLATE_TOKEN` |
| `--exec` | The command run once per task (through the shell) |
| `--parallel` | How many tasks at once. Default 1 |
| `--lease` | Lease seconds; the runner heartbeats every third of it. Default 900 |
| `--state` | Where the stream cursor is kept, so a restart replays what it missed |
| `--once` / `--item` | Take one task (that one), run it, exit: for testing and cron |

## One runner per machine, every agent in one file

A machine runs **one** runner process for all its agents. List them in a TOML file and start
it with `--config`; adding an agent is adding an `[[agent]]` block and restarting the runner,
not installing anything new.

```
python slate_runner.py --config runner.toml
```

```toml
url = "https://slate.example.com"   # Slate, for every agent
env_file = "secrets.env"                        # optional KEY=VALUE file read for every agent

[[agent]]
name = "my-agent"                               # log label; cursor in my-agent-state.json
token_env = "SLATE_TOKEN_MY_AGENT"              # or token_file = "my-agent.token"
exec = "python hermes/run_task.py"              # Hermes Agent over its gateway
parallel = 4                                   # separate card sessions; bounded per-agent capacity
env = { HERMES_API_URL = "http://127.0.0.1:8642", HERMES_API_KEY = "${API_SERVER_KEY}" }

[[agent]]
name = "other-agent"
token_file = "other-agent.token"
exec = "python runner-example/run_task.py"     # an agent service over HTTP
env = { RUNNER_API_URL = "https://agents.example.com", RUNNER_AGENT_ID = "..." }
```

| Key | Where | Meaning |
|---|---|---|
| `url` | top or agent | Slate's address |
| `exec` | top or agent | The command run once per task; it runs in the config file's folder |
| `token_env` / `token_file` | agent | The agent's token: an env var name (default `SLATE_TOKEN`) or a file holding it |
| `env` | agent | Env for the command; `${VAR}` is filled from this process's environment, the top `env_file`, the agent's `env_file` and earlier keys |
| `env_file` | top and agent | `KEY=VALUE` files. The top one only fills `${VAR}` and `token_env` and never reaches a command; the agent's is also passed to that agent's command |
| `parallel`, `lease` | top or agent | As the options above |
| `state` / `state_dir` | agent / top | The cursor file; default `<state_dir>/<name>-state.json` |

Each agent holds its own stream and runs its own tasks with its own environment; one agent's
bad token or outage doesn't stop the others (it is retried every 5 minutes).

**Each command sees only its own agent's secrets.** Agent commands are AI runtimes fed card and
chat text, so a command must never hold another agent's token. A command's environment is
the runner's basic variables (`PATH`, the home and temp folders, the locale, proxies and CA
bundles: `slate_runner.BASE_ENV`), the agent's own `env_file`, its `env` table and `SLATE_*`
(its own `SLATE_TOKEN`). Nothing else of the runner's environment and nothing of the top-level
`env_file` reaches it, and every agent's token is removed from every other agent's environment
wherever it was put (by its variable's name, any `SLATE_TOKEN*`, and by value). Keep every
agent's Slate token in the shared `env_file` (or a `token_file`); put a key one agent's command
reads in that agent's own `env_file`, or name it in its `env` table
(`HERMES_API_KEY = "${API_SERVER_KEY}"`). A command that relied on inheriting some other
variable from the runner's shell gets it the same way: `env = { NAME = "${NAME}" }`. Ctrl-C or SIGTERM
stops them all the same way as below. A full example: `clients/python/runner.example.toml`.
The `exec` paths above are the layout `slate-runner-assets --output <dir>` exports
(`hermes/run_task.py`, `hermes/run_task_cli.py`, `runner-example/run_task.py`); in a checkout
the same files are under `integrations/`. The single-agent options (`--url --exec ...`) work
without a config file.

Set concurrency deliberately: the generic default is one task per agent, not one per card.
The Hermes agent above uses `parallel = 4` because the adapter keeps a separate session for each
card. An agent sharing one conversation (such as `other-agent`, whose service keeps one
conversation per agent) stays at one. Do not raise the
limit for a shared conversation or a shared mutable workspace without accounting for conflicts;
use task dependencies for work that must happen in order. Capacity limits delay pickup, not
lose the request. Every executing card has its own working report and thread.

An agent that must keep working when its chat product's server is down (a repair agent) can
use a one-shot command instead: each turn starts the product's CLI, sends the brief, and reads
the `SLATE:` line from its output. Nothing persists between turns, so a reply turn sends the
answer and the brief again. Run it at `parallel = 1`. The Hermes one is
`integrations/hermes/run_task_cli.py` (README there, "CLI adapter"). The block below is a
separate example, replacing the gateway block for `my-agent` above:

```toml
[[agent]]
name = "my-agent"
token_file = "my-agent.token"
exec = "python hermes/run_task_cli.py"
parallel = 1
env = { HERMES_PROFILE = "my-agent", HERMES_TURN_TIMEOUT = "900" }
```

On startup/reconnect the runner fills all configured slots. A task stays reserved locally
through its take, child process and finish request so parallel workers cannot launch it twice.

Every take carries a private per-attempt `run_id`; the runner persists it before launch and
passes it to heartbeat/finish. A same-account process cannot adopt it without proof. Result
delivery persists the exact body until acknowledged; it never strips fields/proof to force
a refused result through. Retry delivery, not execution. Keep runner/client/adapter copies
pinned to the same release; do not overwrite installed adapters automatically from a moving
branch.

## When a task runs

- **Assigned** with no due date: at once.
- **With a due date**: at its run time. A date-only due date (no time) runs at **06:00 in the workspace timezone
  that morning**, so "due today" is done by the time you look; a due date with a time runs at
  that time. Until then `/work/next` does not hand it to the agent (the due gate).
- **Run now** on the card (or `POST /items/{id}/run`, MCP `slate_item run`): at once, even
  before its run time. The runner takes exactly the task that was pressed
  (`POST /work/next {"item_id": ...}`).
- **An answer on the card**: when the agent finished `blocked` waiting on a person, that
  person's next comment on the card is the agent's next turn. It arrives as a `work` hint
  (`type: comment.assigned`, `comment_id`, `reply: true`) and `/work/next` hands it back as
  `reply`.
- **A chat mention**: a person @mentions the agent in a channel or a thread. The hint carries
  `channel`, `thread_id` (null for the channel itself) and `message_id`; the runner takes that
  turn (`POST /work/next {"channel": ..., "thread_id": ...}`) and runs the command with `chat`
  on stdin (below). `queued: true` on the hint means a turn is already running there for this
  agent and the new mention joins it: no second run. Only mentions by people wake an agent (an
  agent mentioning an agent does not), only agents with wake mode `push`, and only where the
  agent may post. See "A chat turn" below. A turn whose lease ran out (the runner lost it)
  waits for this runner's reconcile; a person's new mention there, or their Clear
  (`message.agent_turn` released and reconciled, with `pending`), hints the waiting mention
  again as a fresh turn. Page mentions (`doc_id`) work the same way.
- **A message in the agent's own chat** (`agent:<agent id>`): a person talking to the agent
  outside any card. For a workspace agent (non-guest role), every message a person posts there
  mentions the agent without typing `@handle`. A guest-role project agent is tagged-only:
  only an explicit `@handle` wakes it, including in threads and follow-ups; human conversation
  and mentions to others do not. An edit wakes a newly added explicit mention, not one kept
  unchanged. It arrives exactly like a chat mention (same hint, same turn, same `chat` on stdin,
  `channel` = `agent:<id>`). Answer in that chat. It may be a question, or a
  request to do something in Slate with the agent's own token. Guests of a project you are
  shared into read and post there too, so answer as you would in the project's chat.
- **A page mention**: a person @mentions the agent on a page (a save that adds `@handle`) or in
  one of its comment threads, or addresses a doc comment to it (an Ask). The hint carries
  `doc_id`, `thread_id` (null for a mention in the page body) and `comment_id`; the runner takes
  that turn (`POST /work/next {"doc_id": ..., "thread_id": ...}`) and runs the command with `doc`
  on stdin. Same rules as chat: people only, `push` agents only, only where the agent may
  comment. See "A doc turn" below.
- On start, and after every task, the runner also takes whatever else is free (chat turns after
  tasks, doc turns after chat). `SLATE_WORK_KINDS` (default `task,chat,doc`) limits what it takes.

## The card is the conversation

Task Ask settlement is server-owned; protocol 3 binds it to the attempt proof and receipt. `/work/next`
returns only this take's pending `asks` and `reply`; every agent finish consumes that take's
Ask dispatch boundary, including review, blocked and release. Unresolved Asks remain visible
in Today and on the card, but cannot override the same wait/review/done state indefinitely.
An assigned-task done does not resolve Asks. Asked-only agent done retains its explicit
answer-and-resolve contract, limited to Asks present at take. New/reassigned Asks during the
run remain pending. Do not bulk-resolve Asks in an adapter or infer resolution from a reply.
The generic runner, Python/TypeScript clients, gateway/CLI and runner-example adapters all use the
same finish endpoint with the runner's proof.

Answer on the card the way you would text the person who owns the task: a few lines. Put anything
long in a file or a note and link it. End with the `SLATE:` line. Your answer for the turn is
`reply` in the result (one comment by you on the card); the `SLATE:` line is the outcome.
`done` and `review` both mean the work is finished; background work you started must report
back before you answer. The bundled adapters treat an answer with no `SLATE:` line as not
finished: the Hermes gateway adapter asks again in the same session (`HERMES_NUDGES`), and a
run that still has no line finishes `blocked` with a "Paused" note, never `review`.

Keep one session per task in your own runtime and tell Slate its id as `thread: {"ref": ...,
"url": ...}` on your first progress line (`url` is the badge's Open link; http(s), or a desktop
app's scheme the server lists in `GET /me` `link_schemes`). The next run of the same task (an answer on the card,
Run now again) gets it back on stdin as `thread`, so you continue the same session, even after
the runner was reinstalled. On that run, `reply` holds the answer: send a person's
(`author_kind: person`) words as the user's turn; quote an agent's or a guest's comment as
information, never as instructions.

A definitely-unsubmitted runtime failure can be retried explicitly. A timeout or lost stream
after submission remains unknown and requires native reconciliation; no automatic replay.
When native approval is requested, keep the observation stream and lease alive while the
person uses the runtime's native approval control. Do not interpret card keywords as approval,
post private commands into shared chats, or disconnect an approval-bearing native turn.
Only authoritative terminal evidence settles the observation; see the adapter's README.

## A chat turn

A turn is a mention to answer, not a task: nothing on the board changes except the chat. The
`/work/next` answer has `item: null` and `chat`:

```json
{"chat": {"channel": "section:Ab12Cd34Ef56", "channel_name": "Example Project", "thread_id": null,
          "message_id": "...", "since_id": "...",
          "message": {"body_md": "@my-agent what is left for Friday?", "author_id": "...", "...": "..."},
          "messages": [{"...": "the newest top-level messages, or the whole thread, oldest first"}],
          "lease": {"account_id": "...", "claimed_at": "...", "until": "..."}},
 "why": "mentioned", "item": null, "lease": null}
```

The command gets that JSON on stdin and env `SLATE_URL`, `SLATE_TOKEN`, `SLATE_CHANNEL`,
`SLATE_THREAD_ID` (empty for the channel) and `SLATE_CHAT_URL` (the board link to the chat or
thread). `message` is the mention this turn answers (`since_id`..`message_id` when several
arrived); `messages` is what the agent may read for context. Progress lines only keep the lease
(there is no card badge). The result line's `reply` is posted as the agent's message in the
channel, in the thread when the mention was in one:

```json
{"outcome": "done", "reply": "Two things: the deck and the invoice. Both open."}
```

`review` counts as `done`. `blocked` or `release` ends the turn with **nothing posted** (the note
goes to the history as `message.agent_turn`), and so does a failed run or a run with no result
line: a shared chat never gets a stray stdout tail, and nothing is retried; the person mentions
the agent again. A normal chat post does not settle a fenced execution. The runner must finish the exact
channel/thread with the run_id returned by its take. A mention that arrives while the turn runs is the next turn
(`pending: true` on the finish answer); the runner picks it up right after.

Nothing here knows what runs: an adapter that keeps one session per card can keep one per
channel-thread the same way, keyed by `SLATE_CHANNEL` + `SLATE_THREAD_ID`.

## A doc turn

The same shape as a chat turn, on a page. The `/work/next` answer has `item: null`, `chat: null`
and `doc`:

```json
{"doc": {"doc_id": "...", "title": "Plan", "kind": "page",
         "path": [{"id": "...", "title": "Example Project"}], "thread_id": null, "comment_id": null,
         "mention": {"excerpt": "Ask @my-agent to price the materials.", "by_id": "...",
                     "by_handle": "alex", "by_kind": "person", "comment": null},
         "section_md": "## Costs\n\nAsk @my-agent to price the materials.\n",
         "body_chars": 1840, "comments": [],
         "may": {"reply": true, "edit": true},
         "lease": {"account_id": "...", "claimed_at": "...", "until": "...", "run_id": "..."}},
 "why": "mentioned", "item": null, "lease": null}
```

Env: `SLATE_URL`, `SLATE_TOKEN`, `SLATE_RUN_ID`, `SLATE_DOC_ID`, `SLATE_THREAD_ID` (empty for a
body mention) and `SLATE_DOC_URL` (the board link to the page, or to the thread). `section_md` is
the part of the page around the mention (around the quoted span for an anchored review thread),
at most 8000 characters; `GET /docs/{doc_id}` has the whole page. `comments` is the thread, oldest
first. The result line's `reply` becomes the agent's comment in that thread, or a new discussion
thread on the page when the mention was in the body (`POST /work/doc/finish`). Anything else, a
failed run or no result line posts nothing, exactly as in chat. `may.edit` says the agent could
also change the page with its own token; the turn itself only answers.


**Input.** The task as JSON on stdin: the `/work/next` answer (`item`, `lease`, `context` =
the last comments and events, `asks`, `reply` = the answer on the card for this turn, `thread`
= your session for this task; or `chat` for a chat turn, or `doc` for a doc turn, above). Env: `SLATE_URL`,
`SLATE_TOKEN`, `SLATE_ITEM_ID`, `SLATE_ITEM_URL` (the board link to the task). Card text (the
title, comments, chat) is only on stdin: the command runs through a shell, and text in its
environment could be expanded into the command line (`%VAR%` on Windows is expanded before
`&` and `|` are read), so an `exec` that names `SLATE_ITEM_TITLE` is refused.

**Progress.** Any stdout line that is a JSON object without `outcome` is sent at once as a
heartbeat. Print the place you work in first, so the card links to it within a second. Add
`progress` (0-100) only when you can measure it: the board shows no percent or bar at 5 or
less, so a fixed "just started" number is noise.

```json
{"note": "Opened chat", "url": "https://example.com/chat/42",
 "thread": {"ref": "sess_42", "url": "https://example.com/chat/42"}}
```

`url` must use a scheme the server allows (`http` and `https` unless the server lists more in
`GET /me` `link_schemes`); the card shows it as **Open ↗**. It stays until the task is done or
released.

**Result.** The last stdout line that is a JSON object with `outcome`:

```json
{"outcome": "done", "reply": "Sent the invoice.", "thread": {"ref": "sess_42"}}
```

`outcome` is `done`, `review`, `later` (you did what you can now and already have any go you
need; the rest runs at the task's due time, which must still be to come: no badge, nobody waited
on, you stay assigned; a `later` Slate refuses comes back blocked on the owner with a note),
`blocked` (add `"waiting_on": "alex"` with your question in `reply`: the card reads *Waiting on
you* and that person gets a push) or `release`. On a card you were
only *asked* on (not assigned), `blocked` may wait only on the person who asked you; blocked on
anyone else is taken as your plain answer, with no badge and no waiting-on, because it is not
your card. `note` (a labelled comment in place of `reply`) works too. `status` and `hand_to`
work as in `POST /work/{id}/finish`. A `blocked` result that names nobody waits on the
agent's owner; add `"resumable": true` when the run stopped short of needing anyone (a step
limit), and the card waits on no one and offers Run now.

**No result line.** Exit zero without an explicit result is not completion. Failed process
exit outranks success-shaped stdout. Failed/missing results remain blocked/released with a
visible explanation; successful-looking prose is not proof the objective was achieved.

## Example command

`clients/python/run_one_example.py`:

```python
import json, os, sys

task = json.load(sys.stdin)
item = task["item"]
print(json.dumps({"note": "Looking at it", "url": os.environ["SLATE_ITEM_URL"]}))
sys.stdout.flush()
# ... the real work ...
print(json.dumps({"outcome": "done", "note": f"Did: {item['title']}"}))
```

For a one-shot agent CLI (Claude Code's `claude -p`, say), use a small wrapper script as the
command, never the task JSON as the prompt (`claude -p "$(cat)"`): that JSON is card and chat
text without the brief's framing (who wrote each comment, comments quoted as information, the
`SLATE:` line to end with), and `$(cat)` does not work under cmd.exe, the runner's shell on
Windows. The wrapper reads the task from stdin, fetches `GET /items/{id}/brief` with
`SLATE_TOKEN`, passes its `text` to the CLI as one argument (an argument list, no shell), and
prints a result line from the `SLATE:` line its answer ends with.
`integrations/hermes/run_task_cli.py` is that wrapper for the Hermes CLI, and a model to copy.

## Stopping and recovery

SIGTERM/Ctrl-C stops admission and waits before terminating local children. That termination
is not proof a remote execution stopped; unknown runs stay quarantined and are not adopted
on restart. A task's reconcile endpoint (MCP work reconcile) requires verified stopped
state and a reason; chat reconciliation uses the original agent identity. Verify native
session/run/control evidence before reconciliation, never simply clear a local fence file.
A person's Clear on the expired badge is the same confirmation from the board, and the agent's
own reconcile wakes its runner. The local `reconciliation_required` record is then no fence:
a fresh grant for that key (the server only gives one once the attempt is settled) replaces
it. A grant for a key the runner is still busy with (an undelivered result, say) is given back
at once with a `stopped` reconcile, never left to expire into a new quarantine.

For a noninterrupting upgrade, create each runner state path with suffix `.drain`, then wait
for its `.status.json` with matching current pid, fresh timestamp, admission_closed=true and
drained=true. Admission stays closed. Pending/unknown results or unstarted persisted claims
prevent a drained receipt. Stop only after all runners are proven drained, install matching
files/server, and remove the drain files before starting fresh processes. Timeout means wait
or abort, not kill active work.

**An undelivered result is resent on its own.** When a finish cannot be delivered (the server
restarting, the network down), the result is saved in the state file and the live loop resends
the identical body, five seconds later and then backing off to five minutes, without running the
command again; `--retry-delivery KEY` (a task id, or the `chat:`/`doc:` key in the state file)
does it by hand and exits 0 delivered, 2 nothing saved for that key, 3 still undelivered. If the
server settles that run first (its lease ran out and a person cleared it, or a person asked
again), its next grant for the card is run, and the stale result is dropped with a log line. A
record that a runner killed mid-run left behind is read as unknown remote state on the next
start, like an interrupted run.

Finish retries return the saved semantic receipt; then take next separately. Keep the local
state private, since it contains execution proof. Direct reports must not clear active
leases. Remote approvals and confirmed cancellation are adapter-specific capabilities, not
features the generic runner can invent.

For remote-runtime adapters, stop/restart **only while their turns are idle**: ending the
local adapter is not proof that a remote agent turn was cancelled. A takeover of an active
remote turn needs adapter-specific reconciliation; do not blindly replay its prompt.

`--once` exits 0 when the task finished done or review, 1 when blocked or released, 2 when
nothing was free, 3 on an error (bad token, Slate unreachable).
