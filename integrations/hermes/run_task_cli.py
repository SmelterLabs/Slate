"""Run one turn of a Slate task as one Hermes CLI command: no gateway, no long-lived session.

A `--exec` command for clients/python/slate_runner.py (docs/agents/runner.md), for an agent
that must keep working when the gateway is down:

    python slate_runner.py --url $SLATE_URL --token-env SLATE_TOKEN \\
        --exec "python integrations/hermes/run_task_cli.py"

Same stdin/stdout contract as run_task.py (docs/agents/runner.md "The command's contract").
Each turn is a fresh process:

    $HERMES_CLI --profile $HERMES_PROFILE chat --quiet --max-turns $HERMES_MAX_TURNS --query <q>

with `HERMES_HOME` removed from the child's environment (the profile picks its own home). The
query is Slate's brief (`GET /items/{id}/brief`). On a reply turn (`reply` on stdin) it is the
answer on the card, then "continue", then the brief again: nothing persists between turns.

The result comes from the `SLATE:` closing line at the end of the command's stdout (the same
parser as run_task.py). No line: `blocked` ("Paused", never `review`), waiting on `HERMES_ASK`,
with the stdout tail as the reply. A non-zero exit or
a turn longer than `HERMES_TURN_TIMEOUT` seconds: `blocked`, waiting on `HERMES_ASK`, with the
stderr tail as the reply.

Env: `HERMES_CLI` (default `hermes`; may hold arguments, e.g. a python interpreter and a
script), `HERMES_PROFILE` (omitted from the command when unset), `HERMES_MAX_TURNS` (default
50), `HERMES_TURN_TIMEOUT` (seconds, default 600), `HERMES_ASK` (who a blocked task waits on;
default the authorized brief's ask, then owner), plus the runner's `SLATE_URL`, `SLATE_TOKEN`.
Needs only `httpx`. See README.md: "CLI adapter". Slate itself knows nothing of this file.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # the gateway adapter beside this file: run_task.py, or a copy named hermes_run_task.py
    from run_task import PAUSED, REPLY_MAX, closing, emit_stdout, quoted
except ImportError:
    from hermes_run_task import PAUSED, REPLY_MAX, closing, emit_stdout, quoted

TAIL = 2000
CAPABILITIES = {
    "adapter": "hermes-cli",
    "version": 1,
    "work_kinds": ["task"],
    "resume": "fresh-process",
    "approvals": "native-ui-only",
    "remote_stop": False,
}
CONTINUE = "Continue the task. End your answer with the SLATE: line, as before."


def command(env: dict[str, str], query: str) -> list[str]:
    """The child's argv. `HERMES_CLI` may be several words; quotes group a path with spaces."""
    cli = env.get("HERMES_CLI") or "hermes"
    if os.name == "nt":  # posix splitting would eat the backslashes of a Windows path
        argv = [
            w[1:-1] if len(w) > 1 and w[0] == w[-1] == '"' else w
            for w in shlex.split(cli, posix=False)
        ]
    else:
        argv = shlex.split(cli)
    if env.get("HERMES_PROFILE"):
        argv += ["--profile", env["HERMES_PROFILE"]]
    argv += ["chat", "--quiet", "--max-turns", str(env.get("HERMES_MAX_TURNS") or 50)]
    return [*argv, "--query", query]


def child_env(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k != "HERMES_HOME"}


def query_for(task: dict[str, Any], brief: str) -> str:
    """The brief on a first turn; on a reply turn the answer, "continue", and the brief again."""
    reply = task.get("reply")
    if not isinstance(reply, dict) or not str(reply.get("body_md") or "").strip():
        return brief
    said = quoted(reply)
    return f"{said}\n\n{CONTINUE}\n\nThe task, for reference:\n\n{brief}"


def _tail(text: str | bytes | None) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return (text or "").strip()[-TAIL:]


def run(
    task: dict[str, Any], env: dict[str, str], *, slate: httpx.Client, emit=emit_stdout
) -> dict:
    if not task.get("item") and task.get("chat") is not None:
        return {"outcome": "release", "note": "Chat is unsupported by the Hermes CLI adapter."}
    if not task.get("item") and task.get("doc") is not None:
        return {
            "outcome": "release",
            "note": "Doc turns are unsupported by the Hermes CLI adapter.",
        }
    item = task.get("item") or {}
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
        return {"outcome": "release", "note": "Unsupported work: a card item id is required."}
    item_id = item["id"]
    ask = env.get("HERMES_ASK") or "owner"
    try:
        params = {"ask": env["HERMES_ASK"]} if env.get("HERMES_ASK") else None
        r = slate.get(f"/api/v1/items/{item_id}/brief", params=params)
        r.raise_for_status()
        brief = r.json()["text"]
        if not isinstance(brief, str) or not brief.strip():
            raise ValueError("empty brief")
        ask = env.get("HERMES_ASK") or r.json().get("ask") or "owner"
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return {
            "outcome": "blocked",
            "waiting_on": ask,
            "reply": "Could not read the authorized Slate brief; no runtime turn was started.",
        }
    timeout = float(env.get("HERMES_TURN_TIMEOUT") or 600)
    emit({"note": "Session started"})
    try:
        done = subprocess.run(
            command(env, query_for(task, brief)),
            env=child_env(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        why = f"The Hermes command ran past {timeout:g} seconds and was stopped."
        tail = _tail(e.stderr)
        return {"outcome": "blocked", "waiting_on": ask, "reply": f"{why}\n\n{tail}".strip()}
    except OSError as e:
        return {"outcome": "blocked", "waiting_on": ask, "reply": f"Could not run Hermes: {e}"}
    if done.returncode != 0:
        why = f"The Hermes command exited with {done.returncode}."
        tail = _tail(done.stderr) or _tail(done.stdout)
        return {"outcome": "blocked", "waiting_on": ask, "reply": f"{why}\n\n{tail}".strip()}
    found = closing(done.stdout)
    if found is None:  # not finished as far as anyone can tell: pause, never review
        return {
            "outcome": "blocked",
            "waiting_on": ask,
            "reply": f"{_tail(done.stdout)}\n\n{PAUSED}".strip(),
        }
    outcome, who, body = found
    out: dict[str, Any] = {"outcome": outcome}
    if body:
        out["reply"] = body[:REPLY_MAX]
    if outcome == "blocked":
        out["waiting_on"] = who or ask
    return out


def main() -> int:
    if "--capabilities" in sys.argv[1:]:
        emit_stdout(CAPABILITIES)
        return 0
    env = dict(os.environ)
    task = json.loads(sys.stdin.read() or "{}")
    with httpx.Client(
        base_url=env.get("SLATE_URL", ""),
        headers={"Authorization": f"Bearer {env.get('SLATE_TOKEN', '')}"},
        timeout=30,
    ) as slate:
        out = run(task, env, slate=slate)
    emit_stdout(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
