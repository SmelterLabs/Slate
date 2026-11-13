"""The email notification loop: one asyncio task running `slate.engine.run`, like slate.pusher.

Each step runs `service.email.process()` in a worker thread: queue new events for the accounts
that chose email, then send whatever has stayed unseen past the delay. A 30-second poll also
runs it, so a due email goes out even when nothing new happens. Runs when SMTP is configured
(`SLATE_SMTP_HOST` and `SLATE_SMTP_FROM`).
"""

from __future__ import annotations

import asyncio
import logging

from slate import db as slate_db
from slate import engine, mailer
from slate.service import email

log = logging.getLogger(__name__)


def step() -> int:
    with slate_db.SessionLocal() as session:
        return email.process(session, mailer.send)


async def run(poll_seconds: float = 30.0) -> None:
    await engine.run("email", step, poll_seconds=poll_seconds)


def start() -> asyncio.Task | None:
    if not mailer.enabled():
        log.info("email notifications off: SLATE_SMTP_HOST / SLATE_SMTP_FROM not set")
        return None
    return asyncio.create_task(run(), name="slate-email")
