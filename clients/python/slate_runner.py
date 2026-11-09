"""slate_runner: runs an agent's Slate tasks by itself. One file; it needs only `httpx`.

    python slate_runner.py --url https://slate.example.com --token-env SLATE_TOKEN \\
        --exec "python run_one.py" [--parallel 1] [--lease 900] [--state ~/.slate-runner.json] \\
        [--once [--item ID]]
    python slate_runner.py --config runner.toml      # every agent on this machine, one process

`--config`: one runner per machine. The TOML file lists the agents (`[[agent]]`: name, token,
command, env), and each gets its own stream and runs in this one process (`load_config`,
`serve`; example: runner.example.toml).

It holds the agent's live stream (`GET /checkin/stream`) and, for each `work` hint (a task was
assigned, came due, or someone pressed Run now), takes that task (`POST /work/next {item_id}`)
and runs `--exec` for it. Slate is the doorbell; this is the one place that launches anything,
and the command is the only agent-specific part.

A chat mention is work too: a `work` hint with `channel` (and `thread_id`) means a person
@mentioned the agent in a chat; the runner takes that turn (`POST /work/next {channel,
thread_id}`) and runs the same command with `chat` instead of `item` on stdin (the channel, the
thread, the mention and the recent messages) and env `SLATE_CHANNEL`, `SLATE_THREAD_ID`,
`SLATE_CHAT_URL`. The result line's `reply` is posted as the agent's message in that channel or
thread (`POST /work/chat/finish`); `done` without a reply or `release` ends the turn with
nothing posted, and so does a failed run (nothing is written into a shared chat by mistake).
One turn per channel-thread at a time: a mention while one runs joins it.

A page mention is the same shape: a `work` hint with `doc_id` (and `thread_id`) means a person
@mentioned the agent on a page or in one of its comment threads; the runner takes it (`POST
/work/next {doc_id, thread_id}`) and runs the command with `doc` on stdin (the page's title and
path, the section around the mention, the thread's comments, the mention and what it may do)
and env `SLATE_DOC_ID`, `SLATE_THREAD_ID`, `SLATE_DOC_URL`. The result line's `reply` becomes
the agent's comment in that thread, or a new discussion thread for a mention in the page body
(`POST /work/doc/finish`); anything else posts nothing, as in chat.

The command gets the task (the `/work/next` answer: `item`, `lease`, `context`) as JSON on
stdin, and env `SLATE_URL`, `SLATE_TOKEN`, `SLATE_ITEM_ID`, `SLATE_ITEM_TITLE`, `SLATE_ITEM_URL`
(the board link). While it runs:

- every stdout line that is a JSON object without `outcome` is progress, sent at once as a
  heartbeat: `{"note": "Opened chat", "url": "https://...", "thread": {"ref": "<session id>"}}`,
  plus `progress` (0-100) only when the agent can measure it (the card shows the note, and
  `url` as an Open link; `thread` is the agent's own session for this task, handed back as
  `thread` on the next run);
- the runner heartbeats every `lease/3` seconds anyway, so the task stays yours.

When it exits, the last stdout line that is a JSON object with `outcome` is the result:
`{"outcome": "done|review|blocked|release", "reply": "...", "waiting_on": "alex",
"url": "...", "thread": {"ref": "..."}}`. `reply` is the agent's answer for this
turn, one comment on the card (the card is the conversation); `note` is the older, labelled
form. A task that waits on someone's answer comes back with `reply` (their comment) and
`thread` on stdin when they answer. An explicit valid result and exit zero are both required
for success. Missing results and nonzero exits report blocked (chat: release, never stdout).
The retained per-attempt proof is sent on heartbeat/finish and in env `SLATE_RUN_ID`.
`SLATE_WORK_KINDS` (a comma list of task, chat, doc; default all three) restricts admission
before a lease is taken; connection setup must configure this to the adapter's actually
supported work kinds. A server too old for `doc` refuses it (422): the runner then drops `doc`
for the rest of its life and carries on with the others.

On start it also takes whatever is already free (anything that came due while it was down).
After each task it takes the next free one. The stream cursor is kept in `--state`, so a
restart replays what it missed. SIGTERM or Ctrl-C: running commands get 30 s, then they are
stopped locally; remote state remains unknown and requires explicit reconciliation, not
automatic same-account resume. Definitive auth/control refusal stops the local child too.
Upgrade without interruption: create the state path with suffix `.drain`, wait for the
matching `.status.json` receipt (fresh pid/at, admission_closed=true, drained=true), then stop
the process. Admission is latched closed. A caller may time out but must not kill active jobs.
Failed result delivery is saved durably and `retry_delivery(key)` resends the identical body
without relaunching the command; it never falls back to unfenced reports or comments.

`--once`: take one task (`--item` for a given one), run it, exit. Exit codes: 0 the task
finished done or review; 1 it finished blocked or released; 2 nothing was free; 3 an error.

Docs: docs/agents/runner.md. Tested in tests/test_clients.py.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx

# Raise with slate.runner_compat.MIN_RUNNER_PROTOCOL when an API change breaks older runners.
RUNNER_PROTOCOL = 3
__version__ = "0.3.0"  # 0.<RUNNER_PROTOCOL>.<patch>; the package version (pyproject)

TAIL = 2000
NOTE_MAX = 4000
STOP_GRACE = 30
DRAIN_GRACE = 1.0
REPLY_MAX = 19000
CHAT_PREFIX = "chat:"
DOC_PREFIX = "doc:"
WORK_KINDS = ("task", "chat", "doc")
RESULT_KEYS = (
    "outcome",
    "note",
    "reply",
    "thread",
    "waiting_on",
    "url",
    "status",
    "hand_to",
    "progress",
)
PROGRESS_KEYS = ("note", "progress", "url", "thread")


class SlateHTTPError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


def _tail(text: str, n: int = TAIL) -> str:
    text = text.strip()
    return text if len(text) <= n else "…" + text[-(n - 1) :]


def chat_key(channel: str, thread_id: str | None) -> str:
    """The runner's reservation key for a chat turn (tasks use the item id)."""
    return f"{CHAT_PREFIX}{channel}|{thread_id or ''}"


def split_chat_key(key: str) -> tuple[str, str | None]:
    channel, _, thread = key[len(CHAT_PREFIX) :].partition("|")
    return channel, thread or None


def doc_key(doc_id: str, thread_id: str | None) -> str:
    """The runner's reservation key for a doc turn."""
    return f"{DOC_PREFIX}{doc_id}|{thread_id or ''}"


def turn_ref(key: str) -> tuple[str, dict[str, Any]] | None:
    """A turn key's route kind and the fields naming it (`chat` -> channel, `doc` -> doc_id,
    both with thread_id); None for a task (an item id)."""
    for prefix, kind, field in ((CHAT_PREFIX, "chat", "channel"), (DOC_PREFIX, "doc", "doc_id")):
        if key.startswith(prefix):
            where, _, thread = key[len(prefix) :].partition("|")
            return kind, {field: where, "thread_id": thread or None}
    return None


def _proof(got: dict[str, Any]) -> str | None:
    lease = got.get("lease") or (got.get("chat") or got.get("doc") or {}).get("lease") or {}
    return lease.get("run_id")


def task_key(task: dict[str, Any] | None) -> str | None:
    """What a `/work/next` answer is about: the item id, the chat turn's key, or None."""
    if not task:
        return None
    if task.get("item"):
        return task["item"]["id"]
    if task.get("chat"):
        return chat_key(task["chat"]["channel"], task["chat"].get("thread_id"))
    if task.get("doc"):
        return doc_key(task["doc"]["doc_id"], task["doc"].get("thread_id"))
    return None


def _json_object(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not (line.startswith("{") and line.endswith("}")):
        return None
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class Runner:
    def __init__(
        self,
        url: str,
        token: str,
        command: str,
        *,
        parallel: int = 1,
        lease: int = 900,
        state_path: str | Path | None = None,
        client: httpx.Client | None = None,
        log: Any = None,
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.api = self.url + "/api/v1"
        self.token = token
        self.command = command
        self.parallel = max(1, parallel)
        self.lease = lease
        self.state_path = Path(state_path).expanduser() if state_path else None
        self.http = client or httpx.Client(timeout=30)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "X-Slate-Runner": f"slate-runner/{RUNNER_PROTOCOL}",
        }
        self.log = log or (lambda msg: print(f"slate-runner: {msg}", file=sys.stderr, flush=True))
        self.env = env
        self.work_kinds = (
            (os.environ if env is None else env)
            .get("SLATE_WORK_KINDS", ",".join(WORK_KINDS))
            .split(",")
        )
        if not self.work_kinds or any(k not in WORK_KINDS for k in self.work_kinds):
            raise ValueError(
                "SLATE_WORK_KINDS is a comma list of task, chat, doc"
            )  # the command's environment (default: this process's)
        self.cwd = str(cwd) if cwd else None
        self._lock = threading.Lock()  # one HTTP call at a time (the client is shared)
        self._state_lock = threading.Condition()
        self._take_lock = threading.Lock()
        # Reserve across take -> spawn -> finish, not just while the child PID exists.
        self._claimed: set[str] = set()
        self._runs = 0
        self._interrupted: set[str] = set()
        self.running: dict[str, subprocess.Popen] = {}
        self.pending: deque[str | None] = deque()
        self.stopping = threading.Event()
        self._me: dict[str, Any] | None = None
        self._workers = 0
        self._records: dict[str, dict[str, Any]] = {}
        self._cursor: str | None = None
        if self.state_path and self.state_path.exists():
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._records = saved.get("runs", {})
            self._cursor = saved.get("cursor")
        self.drain_path = self.state_path.with_suffix(".drain") if self.state_path else None
        self.status_path = self.state_path.with_suffix(".status.json") if self.state_path else None
        self._admission_closed = False

    # --- HTTP -------------------------------------------------------------------------------

    def call(self, method: str, path: str, body: Any = None) -> Any:
        with self._lock:
            r = self.http.request(method, self.api + path, json=body, headers=self.headers)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            raise SlateHTTPError(r.status_code, detail)
        return r.json() if r.content else None

    def me(self) -> dict[str, Any]:
        if self._me is None:
            self._me = self.call("GET", "/me")
        return self._me

    # --- cursor -----------------------------------------------------------------------------

    def cursor(self) -> str | None:
        if self.state_path is None or not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text()).get("cursor")
        except (OSError, ValueError):
            return None

    def save_cursor(self, cursor: str | None) -> None:
        if self.state_path is None or not cursor:
            return
        with self._state_lock:
            self._cursor = cursor
            self._save_state()

    def _save_state(self) -> None:
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"cursor": self._cursor, "runs": self._records}), encoding="utf-8"
            )
            tmp.replace(self.state_path)

    def _record(self, key: str, **values: Any) -> None:
        with self._state_lock:
            self._records[key] = {**self._records.get(key, {}), **values}
            self._save_state()

    def _closed(self) -> bool:
        if self.drain_path and self.drain_path.exists():
            self._admission_closed = True
        return self.stopping.is_set() or self._admission_closed

    def drain_status(self) -> dict[str, bool]:
        """Admission-closed drain receipt; never assumes local stop proves remote stop."""
        with self._take_lock, self._state_lock:
            closed = self._closed()
            unknown = any(r.get("state") != "delivered" for r in self._records.values())
            result = {
                "admission_closed": closed,
                "drained": closed and not (self._claimed or self.running or self._runs or unknown),
            }
            if self.status_path:
                self.status_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.status_path.with_suffix(".tmp")
                tmp.write_text(json.dumps({**result, "pid": os.getpid(), "at": time.time()}))
                tmp.replace(self.status_path)
            return result

    # --- one task ---------------------------------------------------------------------------

    def take(self, item_id: str | None = None) -> dict[str, Any] | None:
        """Take a task (that one, with `item_id`; a chat or doc turn with a `chat:` or `doc:`
        key): the `/work/next` answer, or None."""
        if self._closed():
            return None
        body: dict[str, Any] = {
            "lease_seconds": self.lease,
            "note": "Starting",
            "resume": False,
            "kinds": self.work_kinds,
        }
        ref = turn_ref(item_id) if item_id else None
        if ref is not None:
            if ref[0] not in self.work_kinds:
                return None
            body.update({k: v for k, v in ref[1].items() if v})
        elif item_id:
            body["item_id"] = item_id
        with self._take_lock:
            with self._state_lock:
                if self._closed():
                    return None
                # A `reconciliation_required` record is no fence of its own: the server keeps
                # that attempt quarantined until it is reconciled, so a fresh grant for the key
                # proves it was settled (by this agent's reconcile or a person's Clear).
                active = (
                    self._claimed
                    | self.running.keys()
                    | {
                        k
                        for k, r in self._records.items()
                        if r.get("state") not in ("delivered", "reconciliation_required")
                    }
                )
                if item_id in active:
                    return None
            try:
                got = self.call("POST", "/work/next", body)
            except SlateHTTPError as e:
                if e.status != 422 or "doc" not in self.work_kinds or len(self.work_kinds) == 1:
                    raise
                # A server from before doc turns: carry on with the kinds it knows.
                self.log("this Slate does not hand out doc turns yet; taking task and chat only")
                self.work_kinds = [k for k in self.work_kinds if k != "doc"]
                if "doc_id" in body:
                    return None
                body["kinds"] = self.work_kinds
                got = self.call("POST", "/work/next", body)
            key = task_key(got)
            if key is None:
                return None
            proof = _proof(got)
            if not proof:
                raise SlateHTTPError(
                    426, "server lacks execution fencing proof; upgrade before running"
                )
            if key in active:
                # Never sit on a grant it will not run: an unworked lease expires into a
                # quarantine that strands the card ("execution lease expired").
                self._give_back(key, proof)
                return None
            with self._state_lock:
                if self._records.get(key, {}).get("state") == "reconciliation_required":
                    del self._records[key]  # settled remotely; start a clean record
            self._record(key, run_id=proof, state="taken", remote_state="not_started")
            with self._state_lock:
                self._claimed.add(key)
            return got

    def _env(self, task: dict[str, Any]) -> dict[str, str]:
        env = {
            **(os.environ if self.env is None else self.env),
            "SLATE_URL": self.url,
            "SLATE_TOKEN": self.token,
            "SLATE_RUN_ID": self._records.get(task_key(task), {}).get("run_id", ""),
        }
        item = task.get("item")
        if item:
            return {
                **env,
                "SLATE_ITEM_ID": item["id"],
                "SLATE_ITEM_TITLE": item.get("title") or "",
                "SLATE_ITEM_URL": f"{self.url}/#/list/{item['list_id']}?task={item['id']}",
            }
        doc = task.get("doc")
        if doc:
            thread = doc.get("thread_id") or ""
            return {
                **env,
                "SLATE_DOC_ID": doc["doc_id"],
                "SLATE_THREAD_ID": thread,
                "SLATE_DOC_URL": f"{self.url}/#/docs/{doc['doc_id']}"
                + (f"?comment={thread}" if thread else ""),
            }
        chat = task["chat"]
        thread = chat.get("thread_id") or ""
        return {
            **env,
            "SLATE_CHANNEL": chat["channel"],
            "SLATE_THREAD_ID": thread,
            "SLATE_CHAT_URL": f"{self.url}/#/chat/{chat['channel']}"
            + (f"?thread={thread}" if thread else ""),
        }

    def heartbeat(self, item_id: str, **body: Any) -> None:
        try:
            ref = turn_ref(item_id)
            if ref is not None:
                # A chat or doc turn's heartbeat only keeps the lease: there is no badge.
                self.call(
                    "POST",
                    f"/work/{ref[0]}/heartbeat",
                    {
                        **ref[1],
                        "lease_seconds": self.lease,
                        "run_id": self._records.get(item_id, {}).get("run_id"),
                    },
                )
                return
            self.call(
                "POST",
                f"/work/{item_id}/heartbeat",
                {
                    "lease_seconds": self.lease,
                    **{k: v for k, v in body.items() if v is not None},
                    "run_id": self._records.get(item_id, {}).get("run_id"),
                },
            )
        except (SlateHTTPError, httpx.HTTPError) as e:
            self.log(f"heartbeat for {item_id} failed: {e}")
            if isinstance(e, SlateHTTPError) and e.status in (401, 403, 404, 409, 410, 426):
                self._lost_execution(item_id)

    def _lost_execution(self, key: str) -> None:
        self._record(key, state="reconciliation_required", remote_state="unknown")
        with self._state_lock:
            self._interrupted.add(key)
            proc = self.running.get(key)
        if proc is not None and proc.poll() is None:
            self._stop_local(proc)

    def _stop_local(self, proc: subprocess.Popen) -> None:
        """Local containment only. This is not remote cancellation confirmation."""
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            proc.wait(timeout=5)

    def run(self, task: dict[str, Any]) -> str:
        """Run the command for a taken task (or chat or doc turn) and report back. Returns the
        outcome."""
        item_id = task_key(task) or ""
        with self._state_lock:
            self._runs += 1
        try:
            if self.stopping.is_set():
                self._leave_for_resume(item_id)
                return "release"
            return self._run(task)
        finally:
            with self._state_lock:
                proc = self.running.get(item_id)
                # Even an unexpected failure must not make a live child takeable twice.
                if proc is None or proc.poll() is not None:
                    self.running.pop(item_id, None)
                    self._claimed.discard(item_id)
                self._runs -= 1
                self._state_lock.notify_all()

    def _run(self, task: dict[str, Any]) -> str:
        item_id = task_key(task) or ""
        if task.get("item"):
            self.log(f"running {item_id} {task['item'].get('title')!r}")
        elif task.get("doc"):
            self.log(f"answering a mention on {task['doc'].get('title')!r}")
        else:
            self.log(f"answering a mention in #{task['chat'].get('channel_name') or item_id}")
        # Stop and spawn share this lock: a child is either registered before shutdown's
        # snapshot, or never launched. No HTTP call is made while holding the state lock.
        with self._state_lock:
            if self._closed():
                proc = None
            else:
                proof = _proof(task)
                owned = self._records.get(item_id, {})
                if not proof or proof != owned.get("run_id") or owned.get("state") != "taken":
                    raise SlateHTTPError(409, "attempt is not a locally unstarted, proved take")
                self._record(item_id, state="running", remote_state="unknown")
                # Python 3.12+ is required; encoding/errors are supported. self.command is
                # operator configuration, never chat text (which is supplied on stdin).
                proc = subprocess.Popen(  # nosemgrep: python.lang.compatibility.python36.python36-compatibility-Popen1, python.lang.compatibility.python36.python36-compatibility-Popen2  # noqa: E501
                    self.command,
                    shell=True,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=self._env(task),
                    cwd=self.cwd,
                    start_new_session=os.name == "posix",  # stop the whole command
                )
                self.running[item_id] = proc
        if proc is None:
            if self._admission_closed and not self.stopping.is_set():
                return self._release_unstarted(item_id)
            self._leave_for_resume(item_id)
            return "release"
        result: dict[str, Any] | None = None
        plain: list[str] = []
        errors: list[str] = []
        output_lock = threading.Lock()
        output_closed = threading.Event()

        def read_stdout() -> None:
            nonlocal result
            assert proc.stdout is not None
            with proc.stdout:
                for line in proc.stdout:
                    with output_lock:
                        if output_closed.is_set():
                            continue
                        obj = _json_object(line)
                        if obj is None:
                            plain.append(line)
                        elif "outcome" in obj:
                            result = obj
                        elif any(k in obj for k in PROGRESS_KEYS):
                            progress = {k: obj.get(k) for k in PROGRESS_KEYS}
                            self.heartbeat(item_id, **progress)

        def read_stderr() -> None:
            assert proc.stderr is not None
            with proc.stderr:
                for line in proc.stderr:
                    with output_lock:
                        if not output_closed.is_set():
                            errors.append(line)

        readers = [threading.Thread(target=f, daemon=True) for f in (read_stdout, read_stderr)]
        for t in readers:
            t.start()
        try:
            try:
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(task))
                proc.stdin.close()
            except BrokenPipeError:
                pass
            except OSError as exc:
                # Windows reports EINVAL when a child exits without consuming stdin (the
                # same exception CPython's subprocess._stdin_write tolerates). Still require
                # the usual structured terminal proof; other I/O failures must propagate.
                if os.name != "nt" or exc.errno != errno.EINVAL:
                    raise
            beat = max(5.0, self.lease / 3)
            while True:
                try:
                    proc.wait(timeout=beat)
                    break
                except subprocess.TimeoutExpired:
                    if item_id in self.running:
                        self.heartbeat(item_id)
            # A descendant may inherit the pipes after the shell exits. Drain only briefly.
            deadline = time.monotonic() + DRAIN_GRACE
            for t in readers:
                t.join(timeout=max(0, deadline - time.monotonic()))
        finally:
            # Fence both output and in-flight progress before finish (also on failure).
            # Remaining daemon readers only discard data until EOF; never close a pipe
            # from here, since close itself can block behind the reader's pending read.
            with output_lock:
                output_closed.set()
        with self._state_lock:
            if item_id in self._interrupted:
                return "reconciliation_required"
        body = self._result(task, proc.returncode, result, "".join(plain), "".join(errors))
        body["run_id"] = self._records[item_id]["run_id"]
        path = f"/work/{item_id}/finish"
        ref = turn_ref(item_id)
        if ref is not None:
            path = f"/work/{ref[0]}/finish"
            body.update(ref[1])
        return self._deliver(item_id, path, body)

    def _give_back(self, key: str, proof: str) -> None:
        """Release a grant for a key this runner is still busy with, without touching its
        local record (which may hold an undelivered result)."""
        body = {
            "run_id": proof,
            "state": "stopped",
            "evidence": "Granted while this runner holds an unsettled attempt for it; not started",
        }
        path = f"/work/{key}/reconcile"
        ref = turn_ref(key)
        if ref is not None:
            body.update(ref[1])
            path = f"/work/{ref[0]}/reconcile"
        try:
            self.call("POST", path, body)
        except (SlateHTTPError, httpx.HTTPError) as exc:
            self.log(f"giving back {key} failed: {exc}")

    def _release_unstarted(self, key: str) -> str:
        body = {
            "run_id": self._records[key]["run_id"],
            "state": "stopped",
            "evidence": "Admission closed before any command was spawned",
        }
        path = f"/work/{key}/reconcile"
        ref = turn_ref(key)
        if ref is not None:
            body.update(ref[1])
            path = f"/work/{ref[0]}/reconcile"
        self._record(
            key, state="delivery_failed", remote_state="not_started", path=path, result=body
        )
        try:
            self.call("POST", path, body)
        except (SlateHTTPError, httpx.HTTPError) as exc:
            self.log(f"unstarted take reconciliation failed: {exc}")
            return "delivery_failed"
        self._record(key, state="delivered", remote_state="not_started")
        return "release"

    def _deliver(self, key: str, path: str, body: dict[str, Any]) -> str:
        """Retry identical results only; never translate refusal into an unfenced report."""
        self._record(key, state="delivery_failed", remote_state="terminal", path=path, result=body)
        for attempt in range(3):
            try:
                self.call("POST", path, body)
            except (SlateHTTPError, httpx.HTTPError) as exc:
                self.log(f"finish failed: {exc}")
                if isinstance(exc, SlateHTTPError) and exc.status < 500 and exc.status != 429:
                    break
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
            else:
                self._record(key, state="delivered")
                outcome = body.get("outcome", "release")
                self.log(f"{key} {outcome} (delivered)")
                return outcome
        return "delivery_failed"

    def retry_delivery(self, key: str) -> str:
        """Retry a saved result without executing again, including after restart."""
        row = self._records[key]
        if row.get("state") != "delivery_failed":
            raise ValueError("no pending terminal delivery")
        return self._deliver(key, row["path"], row["result"])

    def _result(
        self,
        task: dict[str, Any],
        code: int,
        result: dict[str, Any] | None,
        out: str,
        err: str,
    ) -> dict[str, Any]:
        if task.get("chat") or task.get("doc"):
            return self._chat_result(code, result, out, err)
        if result is not None and code == 0:
            body = {k: result[k] for k in RESULT_KEYS if result.get(k) is not None}
            if body.get("outcome") not in ("done", "review", "blocked", "release"):
                body = {"outcome": "blocked", "note": f"Unknown outcome in result: {result}"}
        elif code == 0:
            body = {
                "outcome": "blocked",
                "note": "Command exited without an explicit terminal result",
            }
        else:
            note = _tail(err) or _tail(out) or f"exited with code {code}"
            body = {"outcome": "blocked", "note": f"Run failed (exit {code}): {note}"}
        if body["outcome"] == "blocked" and not body.get("waiting_on"):
            who = self._owner(task)
            if who:
                body["waiting_on"] = who
        if body.get("note"):
            body["note"] = _tail(str(body["note"]), NOTE_MAX)
        else:
            body.pop("note", None)
        if body.get("reply"):
            text = str(body["reply"]).strip()
            body["reply"] = text if len(text) <= REPLY_MAX else text[: REPLY_MAX - 1] + "…"
        else:
            body.pop("reply", None)
        if not isinstance(body.get("thread"), dict):
            body.pop("thread", None)
        return body

    def _chat_result(
        self, code: int, result: dict[str, Any] | None, out: str, err: str
    ) -> dict[str, Any]:
        """The `/work/chat/finish` (and `/work/doc/finish`) body: `done` with the result line's
        `reply` (posted in the channel or on the page), else `release` with a note for the
        history. A run without a result line, or
        one that failed, posts nothing: a shared chat never gets a stray stdout tail."""
        body: dict[str, Any]
        if result is not None and code == 0:
            outcome = result.get("outcome")
            reply = str(result.get("reply") or "").strip()
            note = result.get("note")
            if outcome == "done" and reply:
                body = {"outcome": "done"}
                if reply:
                    body["reply"] = (
                        reply if len(reply) <= REPLY_MAX else reply[: REPLY_MAX - 1] + "…"
                    )
                elif note:
                    body["note"] = note
            elif outcome in ("blocked", "release"):
                body = {"outcome": "release", "note": reply or note or f"The agent said {outcome}"}
            else:
                body = {"outcome": "release", "note": f"Unknown outcome in result: {result}"}
        elif code == 0:
            body = {
                "outcome": "release",
                "note": "Command exited without an explicit terminal reply",
            }
        else:
            note = _tail(err) or _tail(out) or f"exited with code {code}"
            body = {"outcome": "release", "note": f"Run failed (exit {code}): {note}"}
        if body.get("note"):
            body["note"] = _tail(str(body["note"]), NOTE_MAX)
        else:
            body.pop("note", None)
        return body

    def _owner(self, task: dict[str, Any]) -> str | None:
        """Who a failed run waits on: the account's `prefs.owner`, else the first person
        assigned to the task."""
        try:
            owner = (self.me().get("prefs") or {}).get("owner")
        except SlateHTTPError:
            owner = None
        if owner:
            return str(owner)
        for a in task["item"].get("assignees") or []:
            if a.get("kind") == "person":
                return a.get("handle")
        return None

    # --- the loop ---------------------------------------------------------------------------

    def once(self, item_id: str | None = None) -> int:
        task = self.take(item_id)
        if task is None:
            self.log("nothing free")
            return 2
        outcome = self.run(task)
        return (
            0
            if outcome in ("done", "review")
            else (3 if outcome in ("delivery_failed", "reconciliation_required") else 1)
        )

    def _work(self, item_id: str | None) -> None:
        """Take and run tasks until none is free: the hinted one first, then any others."""
        try:
            while not self.stopping.is_set():
                with self._state_lock:
                    if item_id is None and self.pending:
                        item_id = self.pending.popleft()
                task = self.take(item_id)
                if task is None:
                    if item_id is None:
                        return
                    item_id = None
                    continue
                self.run(task)
                item_id = None
        except (SlateHTTPError, httpx.HTTPError) as e:
            self.log(f"work loop error: {e}")
        finally:
            with self._state_lock:
                self._workers -= 1
                # A hint can arrive while an empty take is returning. Retire and hand it
                # off atomically with dispatch, including when this worker hit an error.
                if self.pending and not self.stopping.is_set():
                    self._start_worker(self.pending.popleft())
                self._state_lock.notify_all()

    def _start_worker(self, item_id: str | None) -> None:
        # Caller holds _state_lock, so shutdown cannot miss a worker about to start.
        self._workers += 1
        threading.Thread(target=self._work, args=(item_id,), daemon=True).start()

    def dispatch(self, item_id: str | None) -> None:
        """Answer a hint: start a worker if there is room, else remember the task."""
        with self._state_lock:
            if self.stopping.is_set() or item_id in self._claimed or item_id in self.running:
                return
            if self._workers >= self.parallel:
                if item_id not in self.pending:
                    self.pending.append(item_id)
                return
            self._start_worker(item_id)

    def on_message(self, msg: dict[str, Any]) -> None:
        if msg.get("event") == "ready":
            # The live subscription is established. Refill after this boundary so work
            # between the initial sweep and subscribing cannot disappear without a cursor.
            for _ in range(self.parallel):
                self.dispatch(None)
        if msg.get("event") == "work":
            data = msg.get("data") or {}
            key = data.get("item_id")
            if not key and data.get("channel"):
                # A chat mention: the turn is keyed by channel and thread. A `queued` hint names
                # a turn already running here, and dispatch leaves it alone (no second run).
                key = chat_key(data["channel"], data.get("thread_id"))
            if not key and data.get("doc_id"):
                key = doc_key(data["doc_id"], data.get("thread_id"))
            if key and key not in self.running:
                self.dispatch(key)
        self.save_cursor(msg.get("id"))

    def stream(self) -> Any:
        """The agent stream as `{event, id, data}` dicts (SSE, parsed by hand)."""
        params = {"since": self.cursor()} if self.cursor() else {}
        headers = {**self.headers, "Accept": "text/event-stream"}
        with self.http.stream(
            "GET", self.api + "/checkin/stream", params=params, headers=headers, timeout=None
        ) as r:
            if r.status_code >= 400:
                r.read()
                raise SlateHTTPError(r.status_code, r.text)
            fields: dict[str, str] = {}
            for line in r.iter_lines():
                if self.stopping.is_set():
                    return
                if line.strip() == ": ready":
                    yield {"event": "ready", "data": {}}
                if line and not line.startswith(":"):
                    key, _, value = line.partition(":")
                    fields[key] = fields.get(key, "") + value.removeprefix(" ")
                elif not line and "data" in fields:
                    yield {
                        "event": fields.get("event", "message"),
                        "id": fields.get("id"),
                        "data": json.loads(fields["data"]),
                    }
                    fields = {}

    def forever(self) -> None:
        def monitor_drain():
            while not self.stopping.wait(1):
                self.drain_status()

        threading.Thread(target=monitor_drain, daemon=True).start()
        for _ in range(self.parallel):
            self.dispatch(None)  # fill every slot with already-due work
        backoff = 1.0
        while not self.stopping.is_set():
            try:
                for msg in self.stream():
                    backoff = 1.0
                    self.on_message(msg)
            except (SlateHTTPError, httpx.HTTPError, ValueError) as e:
                if isinstance(e, SlateHTTPError) and e.status in (401, 403):
                    self.log(f"stream refused: {e}; check the token")
                    raise
                if isinstance(e, SlateHTTPError) and e.status == 426:
                    self.log(f"OUT OF DATE: {e.detail}")
                    raise  # the supervisor retries every 5 minutes, after an update
                self.log(f"stream dropped: {e}; reconnecting in {backoff:.0f}s")
            if self.stopping.wait(backoff):
                break
            backoff = min(backoff * 2, 60)
            for _ in range(self.parallel):
                self.dispatch(None)  # catch up every slot after reconnecting

    def _leave_for_resume(self, item_id: str) -> None:
        # Never mutate on interruption: idle can erase a replacement holder's lease,
        # and finish(release) unassigns. A still-owned lease can resume on the next start.
        self._record(item_id, state="reconciliation_required", remote_state="unknown")
        self.log(f"stopped {item_id}; remote execution unknown; explicit reconciliation required")

    def stop(self) -> None:
        """Give commands STOP_GRACE seconds, then stop them without mutating interrupted tasks."""
        with self._state_lock:
            self.stopping.set()
            self.pending.clear()
        # A take that was already on the wire must settle before shutdown can finish.
        # Its worker will observe stopping before spawning and leave the lease intact.
        with self._take_lock:
            pass
        deadline = time.monotonic() + STOP_GRACE
        with self._state_lock:
            while self.running and time.monotonic() < deadline:
                self._state_lock.wait(timeout=max(0, deadline - time.monotonic()))
            stopped = {
                item_id: proc for item_id, proc in self.running.items() if proc.poll() is None
            }
            self._interrupted.update(stopped)
        for proc in stopped.values():
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGTERM)
            else:
                # shell=True means terminate() alone kills cmd.exe, not its command.
                # End the tree so the agent and its inherited output pipes are gone too.
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait()
        with self._state_lock:
            self._state_lock.wait_for(lambda: self._workers == 0 and self._runs == 0)
            unfinished = self._claimed | stopped.keys()
        # Workers have settled and output is fenced. Do not alter any server-side lease.
        for item_id in unfinished:
            self._leave_for_resume(item_id)
        with self._state_lock:
            for item_id in unfinished:
                self.running.pop(item_id, None)
                self._claimed.discard(item_id)
            self._interrupted.clear()


# --- many agents, one process ---------------------------------------------------------------

_VAR = re.compile(r"\$\{(\w+)\}")


def read_env_file(path: Path) -> dict[str, str]:
    """`KEY=VALUE` lines (a `.env`): blank lines and `#` comments skipped, quotes trimmed."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip().removeprefix("export ").strip()] = value.strip().strip("\"'")
    return out


def _expand(value: str, env: dict[str, str]) -> str:
    return _VAR.sub(lambda m: env.get(m.group(1), ""), value)


def load_config(path: str | Path) -> list[dict[str, Any]]:
    """Read a runner config (TOML; docs/agents/runner.md) into one dict per agent: `name`,
    `url`, `token`, `command`, `env` (the command's whole environment), `parallel`, `lease`,
    `state` and `cwd`. Relative paths are relative to the config file. `${VAR}` in `exec`,
    `env` values and `url` is filled from the agent's environment: this process's, then
    `env_file` (top level, then the agent's), then the agent's `env` table."""
    import tomllib

    path = Path(path).expanduser().resolve()
    here = path.parent
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))

    def rel(p: str) -> Path:
        q = Path(p).expanduser()
        return q if q.is_absolute() else here / q

    agents = cfg.get("agent") or []
    if not agents:
        raise ValueError(f"{path}: no [[agent]] entries")
    out = []
    names: set[str] = set()
    for a in agents:
        name = a.get("name")
        if not name or name in names:
            raise ValueError(f"{path}: every [[agent]] needs a unique name (got {name!r})")
        names.add(name)
        env = dict(os.environ)
        for f in (cfg.get("env_file"), a.get("env_file")):
            if f:
                env.update(read_env_file(rel(f)))
        for k, v in (a.get("env") or {}).items():
            env[k] = _expand(str(v), env)
        if a.get("token_file"):
            token = rel(a["token_file"]).read_text(encoding="utf-8").strip()
        else:
            token = env.get(a.get("token_env") or "SLATE_TOKEN", "")
        if not token:
            raise ValueError(f"{path}: no token for {name} (set token_env or token_file)")
        url = _expand(str(a.get("url") or cfg.get("url") or ""), env)
        command = a.get("exec") or cfg.get("exec")
        if not url or not command:
            raise ValueError(f"{path}: {name} needs url and exec")
        state_dir = rel(cfg.get("state_dir") or ".")
        out.append(
            {
                "name": name,
                "url": url,
                "token": token,
                "command": _expand(str(command), env),
                "env": env,
                "parallel": int(a.get("parallel") or cfg.get("parallel") or 1),
                "lease": int(a.get("lease") or cfg.get("lease") or 900),
                "state": rel(a["state"]) if a.get("state") else state_dir / f"{name}-state.json",
                "cwd": here,
            }
        )
    return out


def serve(agents: list[dict[str, Any]], stop: threading.Event | None = None) -> None:
    """Run every agent's stream in this one process until `stop` is set (or Ctrl-C / SIGTERM),
    then stop them all. An agent whose token is refused is retried every 5 minutes; the others
    keep going."""
    stop = stop or threading.Event()

    def logger(name: str) -> Any:
        return lambda msg: print(f"slate-runner[{name}]: {msg}", file=sys.stderr, flush=True)

    runners = [
        Runner(
            a["url"],
            a["token"],
            a["command"],
            parallel=a["parallel"],
            lease=a["lease"],
            state_path=a["state"],
            log=logger(a["name"]),
            env=a["env"],
            cwd=a["cwd"],
        )
        for a in agents
    ]

    def keep(r: Runner) -> None:
        while not r.stopping.is_set():
            try:
                r.forever()
            except Exception as e:  # noqa: BLE001 - one agent's trouble must not stop the rest
                r.log(f"stopped: {e}; trying again in 5 minutes")
                if r.stopping.wait(300):
                    return

    threads = [threading.Thread(target=keep, args=(r,), daemon=True) for r in runners]
    for r, t in zip(runners, threads, strict=True):
        r.log(f"serving as runner for {r.command!r}")
        t.start()
    try:
        while not stop.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    stoppers = [threading.Thread(target=r.stop) for r in runners]
    for t in stoppers:
        t.start()
    for t in stoppers:
        t.join()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run agents' Slate tasks with a command.")
    p.add_argument("--config", help="serve every agent in this TOML file (docs/agents/runner.md)")
    p.add_argument("--url", help="Slate's address, e.g. https://slate.example.com")
    p.add_argument("--token-env", default="SLATE_TOKEN", help="env var holding the token")
    p.add_argument("--exec", dest="command", help="command run once per task")
    p.add_argument("--parallel", type=int, default=1, help="tasks at once (default 1)")
    p.add_argument("--lease", type=int, default=900, help="lease seconds (default 900)")
    p.add_argument("--state", default="~/.slate-runner.json", help="where the cursor is kept")
    p.add_argument("--once", action="store_true", help="run one task and exit")
    p.add_argument("--item", help="with --once: this task")
    args = p.parse_args(argv)
    if args.config:
        try:
            agents = load_config(args.config)
        except (OSError, ValueError) as e:
            print(f"slate-runner: {e}", file=sys.stderr)
            return 3
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        serve(agents, stop)
        return 0
    if not args.url or not args.command:
        p.error("--url and --exec are required (or use --config)")
    token = os.environ.get(args.token_env)
    if not token:
        print(f"slate-runner: set {args.token_env} to the agent's token", file=sys.stderr)
        return 3
    runner = Runner(
        args.url,
        token,
        args.command,
        parallel=args.parallel,
        lease=args.lease,
        state_path=args.state,
    )
    try:
        if args.once:
            return runner.once(args.item)

        def on_signal(*_: Any) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, on_signal)
        try:
            runner.forever()
        except KeyboardInterrupt:
            runner.log("stopping")
            runner.stop()
        return 0
    except (SlateHTTPError, httpx.HTTPError) as e:
        print(f"slate-runner: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
