"""The one loop every event-log engine runs: automations, Web Push, email and webhooks.

    await run(name, step, poll_seconds=..., start=None)

Subscribe to the event bus (a doorbell: any change wakes the loop early), call `start` once,
then call `step` again and again: right after a bell, else every `poll_seconds`. A plain `step`
runs in a worker thread (the service layer is synchronous); a coroutine function is awaited
on the loop (the webhook dispatcher starts its attempts from there).

A step that raises is logged and the loop goes on. Each step is reported to
`slate.monitor.engines` under `name` (the Health page's engine line), and when an engine fails
`settings.health_alert_after` steps in a row, or works again after that, the loop records
`health.alert` / `health.recovered` (service/health.py), as the scheduler does for its hooks.
A step isolates its own items (one event or one delivery that fails is booked or skipped, so
it cannot stop the others); what reaches this loop is a failure of the whole step.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from slate import db as slate_db
from slate import monitor
from slate.models import utcnow
from slate.service.bus import bus

log = logging.getLogger(__name__)

Step = Callable[[], Any] | Callable[[], Awaitable[Any]]


async def _call(fn: Step) -> Any:
    if inspect.iscoroutinefunction(fn):
        return await fn()
    return await asyncio.to_thread(fn)


def _alert(name: str, change: str, error: str | None) -> None:
    from slate.service import health

    with slate_db.SessionLocal() as session:
        try:
            health.record_change(session, name, change, error, utcnow(), kind="engine")
        except Exception:
            log.exception("recording %s for engine %s failed", change, name)


async def step_once(name: str, step: Step) -> str | None:
    """Run one step and report it. Returns the error text, or None when it worked."""
    monitor.engines.begin(name)
    started = time.perf_counter()
    error = None
    try:
        await _call(step)
    except Exception as exc:
        log.exception("%s step failed; retrying", name)
        error = f"{type(exc).__name__}: {exc}"[:500]
    change = monitor.engines.finish(name, error=error, ms=(time.perf_counter() - started) * 1000)
    if change:
        await asyncio.to_thread(_alert, name, change, error)
    return error


async def run(name: str, step: Step, *, poll_seconds: float, start: Step | None = None) -> None:
    wake = asyncio.Event()
    async with bus.subscribe() as sub:
        if start is not None:
            await _call(start)

        async def doorbell() -> None:
            async for _ in sub:
                wake.set()

        bell = asyncio.create_task(doorbell())
        try:
            while True:
                wake.clear()
                await step_once(name, step)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), poll_seconds)
        finally:
            bell.cancel()
