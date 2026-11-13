"""What the Health page watches, counted in memory: agent calls, background pollers, engines.

Three process-local recorders, all reset when the process restarts (Slate is one process, and
a restart is itself a health event: the first scheduler tick runs at once):

- `calls`: every authenticated REST request (`slate.fastlane.Timing`, from the token that
  `api.auth` leaves in the request state) and every in-process MCP tool call
  (`mcp_server.LocalBackend`), per token, in one-minute buckets over the last `WINDOW_MIN`
  minutes: calls, client errors (4xx, or an MCP call that failed on its input), server errors
  (5xx, or an MCP call that raised), slow calls (at least `settings.health_slow_ms`) and time.
  The last `RECENT` errors and slow calls are kept as examples.
- `pollers`: one state per scheduler hook (`slate.scheduler.tick` reports each run): last run,
  last success, the error, consecutive failures and whether a run is in progress (a hook that
  hangs shows as stuck). A hook that returns None did not run this tick.
  `finish()` says when the failure streak reaches `settings.health_alert_after`, and when a
  poller that alerted works again, so the scheduler can record `health.alert` /
  `health.recovered` (service/health.py).
- `tasks`: the long-running asyncio engines started by `slate.app.lifespan` (scheduler,
  webhook dispatcher, automations, push, email), so a crashed engine is visible.

Nothing here touches the database; service/health.py turns it into the report.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from slate.models import utcnow
from slate.settings import settings

WINDOW_MIN = 60
RECENT = 50


def _minute(now: float) -> int:
    return int(now // 60)


@dataclass
class _Bucket:
    calls: int = 0
    client_errors: int = 0
    server_errors: int = 0
    slow: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0


@dataclass
class _Token:
    account_id: str
    buckets: dict[int, _Bucket] = field(default_factory=dict)
    last_at: float = 0.0
    last_error: dict[str, Any] | None = None


class Calls:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, _Token] = {}
        self.errors: deque[dict[str, Any]] = deque(maxlen=RECENT)
        self.slow: deque[dict[str, Any]] = deque(maxlen=RECENT)

    def reset(self) -> None:
        with self._lock:
            self._tokens.clear()
            self.errors.clear()
            self.slow.clear()

    def record(
        self,
        token_id: str,
        account_id: str,
        route: str,
        ms: float,
        *,
        status: int = 200,
        door: str = "api",
        error: str | None = None,
        now: float | None = None,
    ) -> None:
        """One call. `status` is the HTTP status (MCP: 200 ok, 400 bad input, 500 raised)."""
        now = time.time() if now is None else now
        slow = ms >= settings.health_slow_ms
        with self._lock:
            tok = self._tokens.get(token_id)
            if tok is None:
                tok = self._tokens[token_id] = _Token(account_id)
            minute = _minute(now)
            b = tok.buckets.get(minute)
            if b is None:
                b = tok.buckets[minute] = _Bucket()
                for old in [m for m in tok.buckets if m <= minute - WINDOW_MIN]:
                    del tok.buckets[old]
            b.calls += 1
            b.total_ms += ms
            b.max_ms = max(b.max_ms, ms)
            tok.last_at = now
            row = {
                "at": now,
                "token_id": token_id,
                "account_id": account_id,
                "door": door,
                "route": route,
                "status": status,
                "ms": round(ms, 1),
            }
            if status >= 500:
                b.server_errors += 1
            elif status >= 400:
                b.client_errors += 1
            if status >= 400:
                err = {**row, "error": (error or "")[:300] or None}
                tok.last_error = err
                self.errors.appendleft(err)
            if slow:
                b.slow += 1
                self.slow.appendleft(row)

    def snapshot(self, now: float | None = None) -> list[dict[str, Any]]:
        """Per token over the last `WINDOW_MIN` minutes, busiest first."""
        now = time.time() if now is None else now
        floor = _minute(now) - WINDOW_MIN
        out = []
        with self._lock:
            for token_id, tok in self._tokens.items():
                live = [b for m, b in tok.buckets.items() if m > floor]
                calls = sum(b.calls for b in live)
                if not calls:
                    continue
                errors = sum(b.client_errors + b.server_errors for b in live)
                out.append(
                    {
                        "token_id": token_id,
                        "account_id": tok.account_id,
                        "calls": calls,
                        "client_errors": sum(b.client_errors for b in live),
                        "server_errors": sum(b.server_errors for b in live),
                        "error_rate": round(errors / calls, 4),
                        "slow": sum(b.slow for b in live),
                        "avg_ms": round(sum(b.total_ms for b in live) / calls, 1),
                        "max_ms": round(max(b.max_ms for b in live), 1),
                        "last_at": tok.last_at,
                        "last_error": dict(tok.last_error) if tok.last_error else None,
                    }
                )
        out.sort(key=lambda r: -r["calls"])
        return out

    def recent(self, which: str, now: float | None = None) -> list[dict[str, Any]]:
        """The kept examples (`errors` or `slow`) from the last `WINDOW_MIN` minutes."""
        now = time.time() if now is None else now
        with self._lock:
            rows = list(self.errors if which == "errors" else self.slow)
        return [r for r in rows if r["at"] > now - WINDOW_MIN * 60]


calls = Calls()


# --- pollers ---------------------------------------------------------------------------------


@dataclass
class Poller:
    key: str
    runs: int = 0
    failures: int = 0
    consecutive: int = 0
    last_run_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    last_ms: float | None = None
    running_since: datetime | None = None
    alerted: bool = False

    def view(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "failures": self.failures,
            "consecutive_failures": self.consecutive,
            "last_run_at": self.last_run_at,
            "last_ok_at": self.last_ok_at,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "last_ms": None if self.last_ms is None else round(self.last_ms, 1),
            "running_since": self.running_since,
            "alerted": self.alerted,
        }


class Pollers:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.states: dict[str, Poller] = {}

    def reset(self) -> None:
        with self._lock:
            self.states.clear()

    def _get(self, key: str) -> Poller:
        p = self.states.get(key)
        if p is None:
            p = self.states[key] = Poller(key)
        return p

    def begin(self, key: str, now: datetime | None = None) -> None:
        with self._lock:
            self._get(key).running_since = now or utcnow()

    def finish(
        self,
        key: str,
        *,
        ran: bool = True,
        error: str | None = None,
        ms: float | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """Record one run. Returns `alert` when this failure makes the streak reach
        `settings.health_alert_after`, `recovered` when a poller that alerted works again."""
        now = now or utcnow()
        with self._lock:
            p = self._get(key)
            p.running_since = None
            if not ran:
                return None
            p.runs += 1
            p.last_run_at = now
            p.last_ms = ms
            if error:
                p.failures += 1
                p.consecutive += 1
                p.last_error = error[:500]
                p.last_error_at = now
                if not p.alerted and p.consecutive >= max(1, settings.health_alert_after):
                    p.alerted = True
                    return "alert"
                return None
            p.consecutive = 0
            p.last_ok_at = now
            if p.alerted:
                p.alerted = False
                return "recovered"
            return None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: p.view() for k, p in self.states.items()}


pollers = Pollers()


def outcome(result: Any) -> tuple[bool, str | None]:
    """(ran, error) from what a scheduler hook returned. Hooks catch their own network errors
    and report them in the result: `{"errors": [...]}` or `{"error": "..."}`."""
    if result is None:
        return False, None
    if isinstance(result, dict):
        errors = result.get("errors")
        if errors:
            return True, "; ".join(str(e) for e in list(errors)[:3])
        if result.get("error"):
            return True, str(result["error"])
    return True, None


# --- engines -----------------------------------------------------------------------------------

# name -> the task, or None when it is turned off or refused to start (with why).
tasks: dict[str, tuple[asyncio.Task | None, str | None]] = {}


def track(name: str, task: asyncio.Task | None, off_reason: str | None = None) -> None:
    tasks[name] = (task, off_reason)


def task_states() -> dict[str, dict[str, Any]]:
    out = {}
    for name, (task, why) in tasks.items():
        if task is None:
            out[name] = {"state": "off", "detail": why}
        elif task.done():
            exc = None if task.cancelled() else task.exception()
            out[name] = {"state": "stopped", "detail": repr(exc) if exc else "stopped"}
        else:
            out[name] = {"state": "running", "detail": None}
    return out
