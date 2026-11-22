"""The in-process scheduler: one asyncio task that calls each hook every N seconds.

    hooks = [recurrences.materialize, due.run_due_checks, reminders.fire_reminders,
             attention.escalate, reminders.send_digests, trash.purge_expired, work.expire_leases,
             EXPIRE_CHAT, EXPIRE_DOC, sessions.notify_expiring,
             chat_files.sweep_unsent]
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
from functools import partial
from typing import Any

from sqlalchemy.orm import Session

from slate import db, engine, mailer, monitor, webpush
from slate.models import utcnow
from slate.service import (
    attention,
    automations,
    chat_files,
    due,
    email,
    health,
    push,
    recurrences,
    reminders,
    sessions,
    trash,
    turns,
    work,
)
from slate.settings import settings

log = logging.getLogger(__name__)

Hook = Callable[[Session, datetime], Any]

EXPIRE_CHAT = partial(turns.expire, "chat")
EXPIRE_DOC = partial(turns.expire, "doc")

hooks: list[Hook] = [
    recurrences.materialize,
    due.run_due_checks,
    reminders.fire_reminders,
    attention.escalate,
    reminders.send_digests,
    trash.purge_expired,
    work.expire_leases,
    EXPIRE_CHAT,
    EXPIRE_DOC,
    sessions.notify_expiring,
    chat_files.sweep_unsent,
]
# Hook -> its key on the Health page (service/health.py POLLERS has the labels).
POLLERS: dict[Hook, str] = {
    recurrences.materialize: "recurring",
    due.run_due_checks: "due",
    reminders.fire_reminders: "reminders",
    reminders.send_digests: "digest",
    attention.escalate: "escalation",
    trash.purge_expired: "trash",
    work.expire_leases: "leases",
    EXPIRE_CHAT: "chat_turns",
    EXPIRE_DOC: "doc_turns",
    sessions.notify_expiring: "sessions",
    chat_files.sweep_unsent: "chat_uploads",
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


def _engine_step(process: Callable[[Session], Any]) -> Any:
    with db.SessionLocal() as session:
        return process(session)


def _start_automations() -> None:
    with db.SessionLocal() as session:
        automations.start(session)


def start_engines() -> list[asyncio.Task]:
    """Register enabled event-log engines with the shared loop and track them on Health."""
    push_enabled = settings.push_enabled and webpush.enabled()
    email_enabled = mailer.enabled()
    smtp_off = "SLATE_SMTP_HOST / SLATE_SMTP_FROM not set"
    if settings.push_enabled and not push_enabled:
        log.warning("push notifications off: SLATE_SECRET_KEY is not set")
    if not email_enabled:
        log.info("email notifications off: SLATE_SMTP_HOST / SLATE_SMTP_FROM not set")
    specs = (
        (
            "automations",
            settings.automations_enabled,
            "SLATE_AUTOMATIONS_ENABLED=false",
            5.0,
            partial(automations.process, send=automations.send),
            _start_automations,
        ),
        (
            "push",
            push_enabled,
            "SLATE_SECRET_KEY is not set" if settings.push_enabled else "SLATE_PUSH_ENABLED=false",
            5.0,
            partial(push.process, send=webpush.send),
            None,
        ),
        ("email", email_enabled, smtp_off, 30.0, partial(email.process, send=mailer.send), None),
    )
    tasks = []
    for name, enabled, off, interval, process, start in specs:
        task = None
        if enabled:
            task = asyncio.create_task(
                engine.run(
                    name, partial(_engine_step, process), poll_seconds=interval, start=start
                ),
                name=f"slate-{name}",
            )
            tasks.append(task)
        monitor.track(name, task, off)
    return tasks
