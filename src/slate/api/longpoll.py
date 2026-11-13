"""Long-polling: answer now if there is something, else wait for the event bus and look again.

    out = await wait_async(lambda: attempt(session), wait, session)   # REST (async routes)
    out = wait_sync(lambda: attempt(session), wait, session)          # MCP (a worker thread)

`attempt()` returns None for "nothing yet". Between attempts the session's transaction is ended,
so the next attempt reads a fresh snapshot (SQLite WAL keeps a reader on its old one). Any
published event wakes a waiter, which then simply asks again: cheap, and never misses a change
because the subscription (or the published count) is taken before the first attempt.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from slate.service.bus import bus
from slate.service.core import in_change

MAX_WAIT = 60  # seconds; below the usual 60-100 s proxy and MCP client timeouts


def _not_in_change(session: Session, wait: float) -> None:
    # A wait inside apply_change() would sleep holding the write lock, and the rollback
    # between attempts would throw away the change's earlier writes.
    if wait > 0 and in_change(session):
        raise RuntimeError("a long-poll cannot wait inside a change (apply_change)")


async def wait_async[T](attempt: Callable[[], T | None], wait: float, session: Session) -> T | None:
    _not_in_change(session, wait)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, wait)
    async with bus.subscribe() as sub:
        while True:
            out = await run_in_threadpool(attempt)
            left = deadline - loop.time()
            if out is not None or left <= 0:
                return out
            await run_in_threadpool(session.rollback)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(sub.__anext__(), left)
                await asyncio.sleep(0.02)  # let a change's burst of events land, then
                sub.drain()  # look once for all of them


def wait_sync[T](attempt: Callable[[], T | None], wait: float, session: Session) -> T | None:
    _not_in_change(session, wait)
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        seq = bus.seq
        out = attempt()
        left = deadline - time.monotonic()
        if out is not None or left <= 0:
            return out
        session.rollback()
        bus.wait_sync(seq, left)
