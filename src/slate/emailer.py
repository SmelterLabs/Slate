"""The email notification loop: one asyncio task, woken by the event bus, like slate.pusher.

Each step runs `service.email.process()` in a worker thread: queue new events for the accounts
that chose email, then send whatever has stayed unseen past the delay. A minute-long poll also
runs it, so a due email goes out even when nothing new happens. Runs when SMTP is configured
(`SLATE_SMTP_HOST` and `SLATE_SMTP_FROM`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from slate import db as slate_db
from slate import mailer
from slate.service import email
from slate.service.bus import bus

log = logging.getLogger(__name__)


def step() -> int:
    with slate_db.SessionLocal() as session:
        return email.process(session, mailer.send)


async def run(poll_seconds: float = 30.0) -> None:
    wake = asyncio.Event()
    async with bus.subscribe() as sub:

        async def doorbell() -> None:
            async for _ in sub:
                wake.set()

        bell = asyncio.create_task(doorbell())
        try:
            while True:
                wake.clear()
                try:
                    await asyncio.to_thread(step)
                except Exception:
                    log.exception("email step failed; retrying")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), poll_seconds)
        finally:
            bell.cancel()


def start() -> asyncio.Task | None:
    if not mailer.enabled():
        log.info("email notifications off: SLATE_SMTP_HOST / SLATE_SMTP_FROM not set")
        return None
    return asyncio.create_task(run(), name="slate-email")
