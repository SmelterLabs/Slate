"""The fast lane: request timing, latency stats, conditional GETs and the event cursor header.

Three pure-ASGI middlewares, installed by `install()` (outermost first):

- `Timing` measures every `/api/v1` and `/mcp` request to its first response byte, adds
  `Server-Timing: app;dur=<ms>` (and `X-RateLimit-Limit`/`-Remaining` for the token bucket
  the request spent from, which `api.auth` leaves in the request state), and keeps the last
  `SAMPLES` durations per route template in memory for `GET /api/v1/stats` (`latency()`).
  Authenticated requests are also counted per token for the Health page (`slate.monitor`).
- GZip (Starlette's, level 1) compresses responses over 1 KB; it leaves
  `text/event-stream` alone.
- `Conditional` gives the cacheable reads (`CACHEABLE`) a weak `ETag` and answers a matching
  `If-None-Match` with `304` before the route runs. Every API `GET` also carries
  `X-Slate-Cursor`: the newest event id when the request began.

Why an ETag can be this cheap: every change to anything a read shows is an event (AGENTS.md
rule 3), and event ids are time-ordered, so `max(events.id)` names the state of the whole
database. The tag is a hash of that cursor, the URL and the caller's token (some reads are per
account). Reads that also depend on the clock (`/today`, `stale=true`, recurrence previews) or
on bookkeeping rows written without an event (token `last_used_at`, webhook deliveries) are not
in `CACHEABLE`, and a long-poll (`wait=`) is never tagged.

The cursor is read *before* the route runs, so a snapshot read with cursor C plus
`GET /events?since=C` (or the SSE stream with `since=C`) misses nothing; at worst an event
written during the read is seen twice.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from typing import Any

from sqlalchemy import func, select
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from slate import db, monitor
from slate.models import Event
from slate.settings import settings

SAMPLES = 1000  # per route template
GZIP_MIN_BYTES = 1024
# Level 1: a 1.1 MB item list shrinks ~8x in ~5 ms; level 9 (Starlette's default) took ~30 ms
# for a few percent more.
GZIP_LEVEL = 1
CURSOR_HEADER = "X-Slate-Cursor"

# GET paths (under /api/v1) whose answer depends only on stored state, the URL and the caller.
CACHEABLE = re.compile(
    r"^/api/v1/(items(/[0-9A-Za-z]{12}"
    r"(/(events|versions|activity|watchers|comments|checklists|attachments|links))?)?"
    r"|lists(/[0-9A-Za-z]{12})?"
    r"|sections|tags|views(/[0-9A-Za-z]{12})?|accounts|favorites|me"
    r"|checkin|docs(/[0-9A-Za-z]{12}(/versions)?)?)$"
)
# `stale=true` depends on the clock; a long-poll (`wait=` > 0, e.g. `/checkin`) answers a
# later state than the cursor its tag would name, and must wait instead of answering 304.
_TIME_DEPENDENT_QUERY = re.compile(r"(^|&)(stale=(1|true)|wait=0*[1-9])", re.IGNORECASE)


def _measured(path: str) -> bool:
    return path.startswith("/api/v1/") or path == "/mcp" or path.startswith("/mcp/")


# --- latency ---------------------------------------------------------------------------------


class _Stats:
    def __init__(self) -> None:
        self.started = time.time()
        self._lock = threading.Lock()
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=SAMPLES))
        self._counts: dict[str, int] = defaultdict(int)

    def add(self, key: str, ms: float) -> None:
        with self._lock:
            self._samples[key].append(ms)
            self._counts[key] += 1

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._counts.clear()
            self.started = time.time()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [(k, sorted(v), self._counts[k]) for k, v in self._samples.items()]
        out = []
        for key, xs, count in rows:
            if not xs:
                continue
            method, _, route = key.partition(" ")
            out.append(
                {
                    "method": method,
                    "route": route,
                    "count": count,
                    "p50_ms": round(_pct(xs, 0.50), 2),
                    "p95_ms": round(_pct(xs, 0.95), 2),
                    "p99_ms": round(_pct(xs, 0.99), 2),
                    "max_ms": round(xs[-1], 2),
                }
            )
        out.sort(key=lambda r: -r["count"])
        return out

    def overall(self) -> dict[str, Any]:
        """Percentiles over every kept sample of every route together (the Health page)."""
        with self._lock:
            xs = sorted(v for d in self._samples.values() for v in d)
        if not xs:
            return {"samples": 0}
        return {
            "samples": len(xs),
            "p50_ms": round(_pct(xs, 0.50), 2),
            "p95_ms": round(_pct(xs, 0.95), 2),
            "p99_ms": round(_pct(xs, 0.99), 2),
            "max_ms": round(xs[-1], 2),
        }


def _pct(xs: list[float], q: float) -> float:
    return xs[min(len(xs) - 1, int(q * len(xs)))]


stats = _Stats()


def latency() -> dict[str, Any]:
    """Per-route latency over the last `SAMPLES` requests of each route, busiest first."""
    return {"since": stats.started, "samples_per_route": SAMPLES, "routes": stats.snapshot()}


def _route_key(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None) or ("/mcp" if scope["path"].startswith("/mcp") else "?")
    if scope["path"].startswith("/api/v1/") and not path.startswith("/api/v1/"):
        path = "/api/v1" + path  # FastAPI leaves the router prefix off nested routes
    return f"{scope['method']} {path}"


class Timing:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _measured(scope["path"]):
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        recorded = False

        async def timed_send(message: Message) -> None:
            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                ms = (time.perf_counter() - start) * 1000
                headers = MutableHeaders(scope=message)
                headers.append("Server-Timing", f"app;dur={ms:.1f}")
                verdict = scope.get("state", {}).get("ratelimit")
                if (
                    verdict is not None
                    and verdict.remaining is not None
                    and "x-ratelimit-limit" not in headers
                ):
                    headers["X-RateLimit-Limit"] = str(verdict.limit)
                    headers["X-RateLimit-Remaining"] = str(verdict.remaining)
                if scope.get("route") is not None or scope["path"].startswith("/mcp"):
                    stats.add(_route_key(scope), ms)  # unknown paths (404s) are not routes
                caller = scope.get("state", {}).get("caller")
                if caller is not None:  # authenticated REST (MCP tool calls record themselves)
                    monitor.calls.record(
                        caller[0], caller[1], _route_key(scope), ms, status=message["status"]
                    )
            await send(message)

        await self.app(scope, receive, timed_send)


# --- conditional GET ---------------------------------------------------------------------------


_MAX_EVENT_ID = select(func.max(Event.id))


_cursor_conn: sqlite3.Connection | None = None


def current_cursor() -> str:
    """The newest event id, or `0` on an empty log.

    Called on the event loop, not in the threadpool: it is one read of the end of the primary
    key index (~20 us), and in WAL mode a read never waits for a writer, so a thread round trip
    would cost more than the query. It has its own plain connection, outside `db`'s statement
    lock, so the loop never waits behind another thread's big read.
    """
    global _cursor_conn
    if str(settings.database_path) == ":memory:":  # tests: one shared connection
        with db.engine.connect() as conn:
            return conn.scalar(_MAX_EVENT_ID) or "0"
    if _cursor_conn is None:
        _cursor_conn = sqlite3.connect(
            settings.database_path, check_same_thread=False, isolation_level=None
        )
    row = _cursor_conn.execute("SELECT max(id) FROM events").fetchone()
    return (row[0] if row else None) or "0"


def etag_for(cursor: str, scope: Scope, authorization: str) -> str:
    h = hashlib.blake2b(digest_size=12)
    for part in (cursor, scope["path"], scope.get("query_string", b"").decode(), authorization):
        h.update(part.encode())
        h.update(b"\0")
    return f'W/"{h.hexdigest()}"'


def _matches(if_none_match: str, etag: str) -> bool:
    return any(tag.strip() in (etag, "*") for tag in if_none_match.split(","))


class Conditional:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "GET"
            or not scope["path"].startswith("/api/v1/")
        ):
            await self.app(scope, receive, send)
            return
        cursor = current_cursor()
        headers = Headers(scope=scope)
        etag = None
        if CACHEABLE.match(scope["path"]) and not _TIME_DEPENDENT_QUERY.search(
            scope.get("query_string", b"").decode()
        ):
            etag = etag_for(cursor, scope, headers.get("authorization", ""))
            inm = headers.get("if-none-match")
            # Only an authenticated caller can hold a tag: the token is part of it.
            if inm and headers.get("authorization") and _matches(inm, etag):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 304,
                        "headers": [
                            (b"etag", etag.encode()),
                            (CURSOR_HEADER.lower().encode(), cursor.encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return

        async def tagged_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h[CURSOR_HEADER] = cursor
                if etag and message["status"] == 200:
                    h["ETag"] = etag
            await send(message)

        await self.app(scope, receive, tagged_send)


def install(app: Any) -> None:
    """Order matters: Starlette wraps the last-added middleware outermost."""
    app.add_middleware(Conditional)
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_BYTES, compresslevel=GZIP_LEVEL)
    app.add_middleware(Timing)
