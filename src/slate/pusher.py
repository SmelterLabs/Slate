"""The Web Push loop: one asyncio task running `slate.engine.run`, like slate.automator.

Each step runs `service.push.process()` in a worker thread, which reads events after its cursor,
decides who hears about each (their inbox reasons, filtered by their push choices) and POSTs
the encrypted notifications (slate.webpush). Each tick drains bounded SQLite delivery retries,
including after restart; failed recipients never replay a successful device's delivery.
Push requires `SLATE_SECRET_KEY` (the VAPID key is derived from it).
"""

from __future__ import annotations

import asyncio
import logging

from slate import db as slate_db
from slate import engine, webpush
from slate.service import push

log = logging.getLogger(__name__)


def step() -> int:
    with slate_db.SessionLocal() as session:
        return push.process(session, webpush.send)


async def run(poll_seconds: float = 5.0) -> None:
    await engine.run("push", step, poll_seconds=poll_seconds)


def start() -> asyncio.Task | None:
    if not webpush.enabled():
        log.warning("push notifications off: SLATE_SECRET_KEY is not set")
        return None
    return asyncio.create_task(run(), name="slate-push")
