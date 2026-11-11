# Runner example: a task on an external agent service

`run_task.py` runs a Slate task on an **agent service you reach over HTTP**. It is a `--exec` command for the generic runner (`clients/python/slate_runner.py`, `docs/agents/runner.md`) and a worked example to copy: the service API it talks to is a small made-up one (below), so replace `start_run` and `get_run` with your service's real calls and keep the rest. The rest is the part that is easy to get wrong: reading the authorized brief, one idempotent start per take, a truthful outcome, and quoting card replies as data.

The task reaches the agent as one message headed `<task title> · <slate id>`, followed by Slate's brief. The card's "Open ↗" link goes to the agent's page (`RUNNER_AGENT_URL`, or the `url` the service returns when the run starts).

**Card conversation:** the agent's answer, minus its `SLATE:` line, comes back as `reply` (one comment by the agent on the card), and the run reports `thread: {ref: <agent id>, url: <agent page>}`. A card reply is quoted as information with its stable author ID, handle, kind, role, comment ID and timestamp; it is never promoted to owner authorization. The authorized brief is fetched on every turn, including replies.

## The example service API

| Need | Call | Shape |
|---|---|---|
| Start a run | `POST /runs` | `{agent_id, text, idempotency_key}` → `{run_id, url?}`. The key (≤200 chars) is the item id plus the take's `claimed_at`, so a retry of the same take starts one run. |
| Read a run | `GET /runs/{run_id}` | → `{status, reply?, error?}` |

Status values: `queued`, `running`, `waiting_input`, `completed`, `failed`, `cancelled`. `reply` is the agent's final text. Auth is an optional `Authorization: Bearer <RUNNER_API_TOKEN>`. A 401/403 blocks the task with a note to check the token; any other error blocks it with the service's own `message` (or the response text), never a traceback.

## Capability and safety contract

`python integrations/runner-example/run_task.py --capabilities` prints JSON and exits without
credentials, network access or a runtime turn:

```json
{"adapter":"runner-example","version":1,"work_kinds":["task"],"resume":"agent-conversation","approvals":"native-ui-only","remote_stop":false}
```

This is an offline declaration, not live readiness or server negotiation. Section/agent chat
work is **unsupported** and returns `release` before any lookup or launch. An absent item ID
also releases. Any HTTP failure, invalid JSON or empty authorized brief blocks before a run is
started; no title-only fallback exists.

Runtime status outranks answer text. Only `completed` permits the closing marker to report
done/review. Failed, cancelled, waiting, timeout or unknown status blocks even if the text
says `SLATE: done`. A completed run without a closing marker also blocks with a "Paused" note.

No approval settlement or remote stop is implemented. Use the service's own controls and
confirm the exact run's terminal state before retrying unknown work. Adapter exit, timeout
and local process termination are not evidence that a remote run stopped.

The generic runner finishes each take through Slate, so the adapter makes no Ask-resolution
calls. A finish consumes only the take's Ask dispatches, leaving questions visible until
explicitly resolved; new or reassigned Asks during a run remain pending.

## Set up

Each agent is one `[[agent]]` in the machine's runner config (`docs/agents/runner.md`), with
`exec = "python runner-example/run_task.py"` (the path to this file from the config's folder)
and `env = { RUNNER_AGENT_ID = "...", RUNNER_API_URL = "..." }`.
Single agent, without a config file:

```
export SLATE_URL=https://slate.example.com SLATE_TOKEN=<the agent's Slate token>
export RUNNER_API_URL=https://agents.example.com RUNNER_AGENT_ID=<the agent's id>
export RUNNER_API_TOKEN=<the service's token>          # optional
python clients/python/slate_runner.py --url $SLATE_URL --token-env SLATE_TOKEN \
    --exec "python integrations/runner-example/run_task.py" --state ~/.slate-runner-my-agent.json
```

Optional: `RUNNER_AGENT_URL` (the Open link; default `<RUNNER_API_URL>/agents/<agent id>`), `RUNNER_ASK` (who a blocked task waits on; default: the brief's, which is the workspace owner), `RUNNER_TURN_TIMEOUT` (seconds, default 3600).

## What a run does

1. Validates the card input and fetches a nonempty authorized brief (`GET /items/{id}/brief`); a failure blocks without runtime work.
2. Starts a run with the title line plus the brief (or, on a later turn, the quoted card reply).
3. Prints a progress line with `url` = the agent's page.
4. Polls `GET /runs/{run_id}` every 5 s until it is `completed`, `failed`, `cancelled` or `waiting_input`, or the timeout passes.
5. Prints the result: only after confirmed completion does `SLATE: done | review | later | blocked waiting_on X` decide the outcome. The remaining reply is the card comment.

Tests: `test_runner_example.py` runs against a fake service (`httpx.MockTransport`). CI runs `uv run pytest -q integrations` as its own step.
