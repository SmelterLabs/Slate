"""The Web Push loop: one asyncio task running `slate.engine.run`, like slate.automator.

Each step runs `service.push.process()` in a worker thread, which reads events after its cursor,
decides who hears about each (their inbox reasons, filtered by their push choices) and POSTs
the encrypted notifications (slate.webpush). Each tick drains bounded SQLite delivery retries,
including after restart; failed recipients never replay a successful device's delivery.
Once a minute it also runs `push.renudge()`: chat
still unread a while after its newest message gets one more push. What was already unread at
start is seeded as nudged, so a restart never replays old nudges. Runs when `SLATE_PUSH_ENABLED` and
`SLATE_SECRET_KEY` is set (the VAPID key is derived from it).
"""

from __future__ import annotations

import asyncio
import logging
import time

from slate import db as slate_db
from slate import engine, webpush
from slate.service import push

log = logging.getLogger(__name__)


RENUDGE_EVERY = 60.0

_nudged: push.Nudged = {}
_last_renudge: float | None = None


def step() -> int:
    global _last_renudge
    with slate_db.SessionLocal() as session:
        sent = push.process(session, webpush.send)
        now = time.monotonic()
        if _last_renudge is None:
            push.renudge(session, webpush.send, _nudged, seed=True)
            _last_renudge = now
        elif now - _last_renudge >= RENUDGE_EVERY:
            sent += push.renudge(session, webpush.send, _nudged)
            _last_renudge = now
        return sent


async def run(poll_seconds: float = 5.0) -> None:
    await engine.run("push", step, poll_seconds=poll_seconds)


def start() -> asyncio.Task | None:
    if not webpush.enabled():
        log.warning("push notifications off: SLATE_SECRET_KEY is not set")
        return None
    return asyncio.create_task(run(), name="slate-push")
