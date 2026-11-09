"""The in-process scheduler: one asyncio task that calls each hook every N seconds.

    hooks = [recurrences.materialize, notify.due_events_tick, notify.reminders_tick,
             notify.digest_tick, trash.purge_expired, work.expire_leases,
             chatwake.expire, docwake.expire, sessions.notify_expiring]
    last_tick_at                          # UTC time of the last finished tick (for /healthz)
    POLLERS                               # each hook's key on the Health page

A hook is `fn(session, now) -> Any`. Each tick runs every hook in a worker thread (the service
layer is synchronous), each with its own fresh session that is closed afterwards, so nothing
holds a transaction between ticks. A hook that raises is logged and the rest still run. Hooks
must be idempotent: a tick can repeat after a restart, and both are. Order matters: instances
spawned this tick get their due events in the same tick.

Each run is reported to `slate.monitor.pollers` (the Health page): a hook that raises, or
returns `{"errors": [...]}` / `{"error": ...}` (a hook catches its own network errors),
failed; one that returns None did not run. When a poller fails
`settings.health_alert_after` runs in a row, or works again after that, the tick records
`health.alert` / `health.recovered` (service/health.py), which owners and admins get in their
inbox.

The webhook dispatcher is not a hook: it needs a ~1 s cadence and wakes early on the event bus,
so it is its own asyncio task (`notify.start_dispatcher()`), also started by `lifespan`.

Started and stopped by `slate.app.lifespan` when `settings.scheduler_enabled`; the interval is
`settings.scheduler_interval_seconds` (default 60).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from slate import db, monitor, notify
from slate.models import utcnow
from slate.service import chatwake, docwake, health, recurrences, sessions, trash, work
from slate.settings import settings

log = logging.getLogger(__name__)

Hook = Callable[[Session, datetime], Any]

hooks: list[Hook] = [
    recurrences.materialize,
    notify.due_events_tick,
    notify.reminders_tick,
    notify.digest_tick,
    trash.purge_expired,
    work.expire_leases,
    chatwake.expire,
    docwake.expire,
    sessions.notify_expiring,
]
# Hook -> its key on the Health page (service/health.py POLLERS has the labels).
POLLERS: dict[Hook, str] = {
    recurrences.materialize: "recurring",
    notify.due_events_tick: "due",
    notify.reminders_tick: "reminders",
    notify.digest_tick: "digest",
    trash.purge_expired: "trash",
    work.expire_leases: "leases",
    chatwake.expire: "chat_turns",
    docwake.expire: "doc_turns",
    sessions.notify_expiring: "sessions",
}
last_tick_at: datetime | None = None
_task: asyncio.Task | None = None


def tick(now: datetime | None = None) -> datetime:
    """Run every hook once at `now` (default: the current time). Returns `now`."""
    global last_tick_at
    now = now or utcnow()
    for hook in list(hooks):
        key = POLLERS.get(hook) or getattr(hook, "__name__", str(hook))
        monitor.pollers.begin(key)
        start = time.perf_counter()
        ran, error = True, None
        # db.SessionLocal is read at call time so tests can point it elsewhere.
        with db.SessionLocal() as session:
            try:
                ran, error = monitor.outcome(hook(session, now))
            except Exception as exc:
                log.exception("scheduler hook %s failed", getattr(hook, "__name__", hook))
                error = f"{type(exc).__name__}: {exc}"
        change = monitor.pollers.finish(
            key, ran=ran, error=error, ms=(time.perf_counter() - start) * 1000, now=now
        )
        if change:
            with db.SessionLocal() as session:
                try:
                    health.record_change(session, key, change, error, now)
                except Exception:
                    log.exception("recording %s for %s failed", change, key)
    last_tick_at = now
    return now


async def _loop(interval: float) -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:  # tick already isolates hooks; this guards the loop itself
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


def running() -> bool:
    return _task is not None and not _task.done()


def start(interval: float | None = None) -> asyncio.Task:
    """Start the loop on the running event loop (first tick immediately). Idempotent."""
    global _task
    if running():
        return _task
    seconds = settings.scheduler_interval_seconds if interval is None else interval
    _task = asyncio.get_running_loop().create_task(_loop(seconds), name="slate-scheduler")
    return _task


async def stop() -> None:
    """Cancel the loop and wait for it. A tick already inside a hook finishes its thread."""
    global _task
    task, _task = _task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
