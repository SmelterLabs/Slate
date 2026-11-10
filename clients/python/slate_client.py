"""A one-file Python client for Slate. Copy it next to your agent; it needs only `httpx`.

    from slate_client import Slate, Conflict

    slate = Slate("https://slate.example.com", token, session_ref="nightly-42")
    task = slate.next(wait=30)             # take your best free task (null: none came)
    while task["item"]:
        item = task["item"]
        ...                                # do the work; slate.heartbeat(item["id"]) if long
        task = slate.finish(item["id"], "done", note="Shipped: https://...",
                            next={"wait": 30})["next"]

Or let it run the loop: `slate.work_forever(handle)` (see its docstring). React instead of
polling with `slate.agent_stream()`: news that concerns you and hints that work is free.

What it does for you:

- One keep-alive HTTP/1.1 connection, gzip, a 30 s timeout.
- `X-Slate-Session-Ref` on every request when `session_ref` is set.
- ETags: every GET remembers its `ETag` and sends `If-None-Match`; a `304` returns the
  remembered body, so polling an unchanged board costs one tiny round trip and no rate limit.
- The check-in cursor: `checkin()` keeps `next_since` and sends it next time, and follows
  `has_more` so one call returns all pending news.
- `429`: waits `Retry-After` seconds and retries (up to `max_retries`), then raises.
- Errors as exceptions: `NotFound`, `Conflict`, `Forbidden`, `Unauthorized`, `Invalid`,
  `RateLimited`, all `SlateError` with `.status` and `.detail`.

Docs: docs/agents/quickstart.md in the Slate repo. Tested in tests/test_clients.py.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = [
    "Checkin",
    "Conflict",
    "Forbidden",
    "Invalid",
    "NotFound",
    "RateLimited",
    "Slate",
    "SlateError",
    "Unauthorized",
]


# The server pings every live stream every 15 s. A stream that hears nothing (not even a ping)
# for this long is a dead connection that never closed (a sleeping host, a dropped tunnel), so it
# is abandoned and reconnected instead of waiting forever.
STREAM_READ_TIMEOUT = 60.0
_STREAM_TIMEOUT = httpx.Timeout(30.0, read=STREAM_READ_TIMEOUT)


class SlateError(Exception):
    def __init__(self, status: int, detail: Any, failed_op: int | None = None) -> None:
        self.status = status
        self.detail = detail
        self.failed_op = failed_op
        super().__init__(f"{status}: {detail}")


class Unauthorized(SlateError):
    """401: no token, or it is invalid or revoked."""


class Forbidden(SlateError):
    """403: the token's scope is too low."""


class NotFound(SlateError):
    """404."""


class Conflict(SlateError):
    """409: e.g. a claim another account holds."""


class Invalid(SlateError):
    """422: the request is invalid; `detail` says why."""


class RateLimited(SlateError):
    """429 after `max_retries` waits."""


_ERRORS = {401: Unauthorized, 403: Forbidden, 404: NotFound, 409: Conflict, 422: Invalid}


@dataclass
class Checkin:
    queue: list[dict[str, Any]] = field(default_factory=list)
    news: list[dict[str, Any]] = field(default_factory=list)
    next_since: str | None = None


class Slate:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        session_ref: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        since: str | None = None,
    ) -> None:
        """`base_url` is the board's address (`https://host:port`), without `/api/v1`.
        `client` lets you pass your own httpx-compatible client (tests pass a TestClient).
        `since` resumes the check-in cursor from a previous run (`slate.since`)."""
        self.api = base_url.rstrip("/") + "/api/v1"
        self.headers = {"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip"}
        if session_ref:
            self.headers["X-Slate-Session-Ref"] = session_ref
        self.http = client or httpx.Client(timeout=timeout)
        self.max_retries = max_retries
        self.since = since
        self.me_id: str | None = None
        self._etags: dict[str, tuple[str, Any]] = {}
        self.last_response: Any = None

    # --- transport ----------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        cache: bool = True,
    ) -> Any:
        """One call. Returns the decoded JSON (None for 204). GETs use the ETag cache."""
        url = self.api + path
        query = {k: _param(v) for k, v in (params or {}).items() if v is not None}
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(query.items()))
        headers = dict(self.headers)
        cached = self._etags.get(key) if method == "GET" and cache else None
        if cached:
            headers["If-None-Match"] = cached[0]
        for attempt in range(self.max_retries + 1):
            r = self.http.request(method, url, params=query, json=json_body, headers=headers)
            self.last_response = r
            if r.status_code != 429:
                break
            if attempt == self.max_retries:
                raise RateLimited(429, _detail(r))
            time.sleep(float(r.headers.get("retry-after", "1")))
        if r.status_code == 304 and cached:
            return cached[1]
        if r.status_code >= 400:
            body = _json(r)
            detail = body.get("detail") if isinstance(body, dict) else r.text
            failed = body.get("failed_op") if isinstance(body, dict) else None
            raise _ERRORS.get(r.status_code, SlateError)(r.status_code, detail, failed)
        if r.status_code == 204 or not r.content:
            return None
        data = r.json()
        etag = r.headers.get("etag")
        if method == "GET" and cache and etag:
            self._etags[key] = (etag, data)
        return data

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("POST", path, params=params, json_body=body)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, json_body=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    # --- who and what -------------------------------------------------------------------

    def me(self) -> dict[str, Any]:
        me = self.get("/me")
        self.me_id = me["account"]["id"]
        return me

    def checkin(self, *, limit: int = 50) -> Checkin:
        """Your queue and all news since the last check-in; advances `self.since`."""
        out = Checkin()
        while True:
            page = self.get("/checkin", since=self.since, limit=limit)
            out.queue = page["queue"]
            out.news.extend(page["news"])
            if page.get("next_since"):
                self.since = page["next_since"]
            out.next_since = self.since
            if not page.get("has_more"):
                return out

    def agents(self) -> list[dict[str, Any]]:
        return self.get("/agents")

    # --- items --------------------------------------------------------------------------

    def items(self, **filters: Any) -> list[dict[str, Any]]:
        """`GET /items` with any filters: list_id, assignee, tag, status, q, fields=[...]..."""
        return self.get("/items", **filters)

    def item(self, item_id: str) -> dict[str, Any]:
        return self.get(f"/items/{item_id}")

    def create(self, list_id: str, title: str, **fields: Any) -> dict[str, Any]:
        """File ONE item (never children; pass parent_id for one subtask)."""
        return self.post("/items", {"list_id": list_id, "title": title, **fields})

    def update(self, item_id: str, **changes: Any) -> dict[str, Any]:
        """Change only these fields; None clears one."""
        return self.patch(f"/items/{item_id}", changes)

    def comment(self, item_id: str, body_md: str) -> dict[str, Any]:
        return self.post(f"/items/{item_id}/comments", {"body_md": body_md})

    # --- reports: the agent loop --------------------------------------------------------

    def report(self, item_id: str, state: str, note: str | None = None, **kw: Any) -> dict:
        """working | blocked | review | done | idle; kw: progress, comment, complete,
        waiting_on, assign_me, claim."""
        body = {"state": state, **kw}
        if note is not None:
            body["note"] = note
        return self.post(f"/items/{item_id}/report", body)

    def claim(self, item_id: str, note: str | None = None, **kw: Any) -> dict[str, Any]:
        """Take the item (and its assignment) unless another account holds it: `Conflict`."""
        return self.report(item_id, "working", note, claim=True, **kw)

    def progress(self, item_id: str, note: str, percent: int | None = None) -> dict:
        return self.report(item_id, "working", note, progress=percent)

    def block(self, item_id: str, note: str, waiting_on: str | None = None) -> dict:
        return self.report(item_id, "blocked", note, waiting_on=waiting_on)

    def review(self, item_id: str, note: str) -> dict[str, Any]:
        return self.report(item_id, "review", note)

    def done(self, item_id: str, note: str | None = None, **kw: Any) -> dict[str, Any]:
        """Clear your badge and move the item to its list's done status."""
        return self.report(item_id, "done", note, **kw)

    # --- the work loop: next, heartbeat, finish -----------------------------------------

    def next(self, *, wait: int = 0, **opts: Any) -> dict[str, Any]:
        """Take your best free task (`POST /work/next`): `{item, lease, why, context,
        remaining}`; `item` is None when none is free (after waiting up to `wait` seconds).
        opts: lease_seconds, pool, list_id, section_id, tag, resume, note, context."""
        return self.post("/work/next", {"wait": wait, **opts})

    def heartbeat(self, item_id: str, **kw: Any) -> dict[str, Any]:
        """Keep holding the task; kw: lease_seconds, note, progress. `Conflict` = you lost it."""
        return self.post(f"/work/{item_id}/heartbeat", kw)

    def finish(
        self, item_id: str, outcome: str = "done", note: str | None = None, **kw: Any
    ) -> dict[str, Any]:
        """Report back in one call: outcome done | review | blocked | release; kw: status,
        hand_to, waiting_on, progress, comment, next={...} (take the next task too).
        Returns `{item, next}`."""
        body = {"outcome": outcome, **kw}
        if note is not None:
            body["note"] = note
        return self.post(f"/work/{item_id}/finish", body)

    def held(self) -> list[dict[str, Any]]:
        """The tasks you hold now, with their leases."""
        return self.request("GET", "/work", cache=False)

    # --- batch and events ---------------------------------------------------------------

    def batch(
        self, ops: list[dict[str, Any]], *, atomic: bool = True, views: str = "final"
    ) -> dict[str, Any]:
        """Up to 100 MCP-shaped ops in one round trip; `$ref.id` reuses an earlier result.
        Tasks in the results show the end of the batch (`views="each"`: as each op left them)."""
        return self.post("/batch", {"ops": ops, "atomic": atomic, "views": views})

    def events(self, since: str | None = None, **filters: Any) -> dict[str, Any]:
        return self.get("/events", since=since, **filters)

    def stream(self, since: str | None = None, **filters: Any) -> Iterator[dict[str, Any]]:
        """Live events from the SSE stream, replaying everything after `since` first.
        Reconnect by calling again with the last event's `id`."""
        query = {k: _param(v) for k, v in {"since": since, **filters}.items() if v is not None}
        headers = {**self.headers, "Accept": "text/event-stream"}
        with self.http.stream(
            "GET",
            self.api + "/events/stream",
            params=query,
            headers=headers,
            timeout=_STREAM_TIMEOUT,
        ) as r:
            if r.status_code >= 400:
                r.read()
                raise _ERRORS.get(r.status_code, SlateError)(r.status_code, _detail(r))
            data: list[str] = []
            for line in r.iter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    yield json.loads("\n".join(data))
                    data = []

    def agent_stream(
        self, since: str | None = None, *, pool: bool = False
    ) -> Iterator[dict[str, Any]]:
        """Your check-in, live (`GET /checkin/stream`): yields `{"event": "news" | "work",
        "id": cursor, "data": {...}}`. `news` concerns you (with `why`); `work` means a task you
        could take with `next()` just became free. Replays what you missed after `since`;
        reconnect by calling again with the last `id`."""
        query = {k: _param(v) for k, v in {"since": since, "pool": pool}.items() if v is not None}
        headers = {**self.headers, "Accept": "text/event-stream"}
        with self.http.stream(
            "GET",
            self.api + "/checkin/stream",
            params=query,
            headers=headers,
            timeout=_STREAM_TIMEOUT,
        ) as r:
            if r.status_code >= 400:
                r.read()
                raise _ERRORS.get(r.status_code, SlateError)(r.status_code, _detail(r))
            fields: dict[str, str] = {}
            for line in r.iter_lines():
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

    # --- the whole loop -----------------------------------------------------------------

    def work_forever(
        self,
        handle: Callable[[dict[str, Any]], str | None],
        *,
        wait: int = 30,
        lease_seconds: int = 900,
        once: bool = False,
        **scope: Any,
    ) -> None:
        """The work loop: take a task, call `handle(task)`, report back, take the next one.

        `task` is what `next()` returns (`task["item"]`, `task["context"]`). `handle` returns
        an optional result note: the task is then finished `done` with it, and the same call
        takes the next task. If it raises, the task is finished `blocked` with the error. While
        `handle` runs, a heartbeat keeps the lease every `lease_seconds / 3`. With nothing to
        do, each `next()` waits up to `wait` seconds for work. scope: pool, list_id, tag...
        `once=True` stops when no task is free (for cron jobs and tests).
        """
        opts = {"lease_seconds": lease_seconds, "kinds": ["task"], **scope}
        task = self.next(wait=wait, **opts)
        while True:
            item = task.get("item")
            if not item:
                if once:
                    return
                task = self.next(wait=wait, **opts)
                continue
            proof = (task.get("lease") or {}).get("run_id")
            if not proof:
                raise SlateError(426, "server lacks execution fencing proof")
            stop, cancelled = threading.Event(), threading.Event()
            lost: list[Exception] = []
            # Cooperative cancellation: a Python callback cannot be forcibly stopped safely.
            # Handlers must observe this event before external side effects.
            task["cancelled"] = cancelled

            def beat_loop(stop=stop, item=item, proof=proof, lost=lost, cancelled=cancelled):
                while not stop.wait(lease_seconds / 3):
                    try:
                        self.heartbeat(item["id"], run_id=proof, lease_seconds=lease_seconds)
                    except (SlateError, httpx.HTTPError) as exc:
                        lost.append(exc)
                        cancelled.set()
                        return

            beat = threading.Thread(target=beat_loop, daemon=True)
            beat.start()
            outcome, result = "done", None
            try:
                result = handle(task)
            except Exception as exc:  # noqa: BLE001 - report only with current execution proof
                outcome, result = "blocked", f"Failed: {exc}"[:4000]
            finally:
                stop.set()
                beat.join()
            if lost:
                raise lost[0]
            self.heartbeat(item["id"], run_id=proof, lease_seconds=lease_seconds)
            self.finish(item["id"], outcome, result, run_id=proof)
            task = self.next(wait=wait, **opts)

    def run_forever(
        self,
        handle: Callable[[dict[str, Any]], str | None],
        *,
        interval: float = 30.0,
        on_news: Callable[[dict[str, Any]], None] | None = None,
        once: bool = False,
    ) -> None:
        """Check in every `interval` seconds; claim each queue item and call `handle(item)`.

        `handle` returns an optional result comment; when it returns, the item is reported
        `done`. If it raises, the item is reported `blocked` with the error and skipped for the
        rest of this run. Items another agent holds are skipped. `on_news` sees each news
        entry. `once=True` does a single pass (for cron jobs and tests).
        """

        def handler(task):
            if on_news:
                for entry in self.checkin().news:
                    on_news(entry)
            return handle({**task["item"], "cancelled": task["cancelled"]})

        self.work_forever(handler, wait=0 if once else min(60, max(0, int(interval))), once=once)


def _param(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple | set):
        return ",".join(str(v) for v in value)
    return value


def _json(r: Any) -> Any:
    try:
        return r.json()
    except ValueError:
        return None


def _detail(r: Any) -> Any:
    body = _json(r)
    return body.get("detail") if isinstance(body, dict) else r.text
