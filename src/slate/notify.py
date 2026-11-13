"""Outbound notifications: the webhook dispatcher (HTTP side) and the due-event scheduler hook.

The dispatcher is one asyncio task started in the app lifespan. Its loop:

1. fan out every event committed since its cursor into `pending` delivery rows
   (`service.deliveries.fan_out`; the bus subscription is only a doorbell that wakes it early),
2. claim due rows up to `webhook_concurrency` in flight, and send each in a worker thread,
3. once an hour (`PRUNE_EVERY`), prune finished delivery rows older than the retention
   (`service.deliveries.prune`, at most one batch per tick),
4. sleep until the doorbell rings or `poll_seconds` passes (retries come due on the poll).

Each attempt POSTs the stored body with `X-Slate-Event`, `X-Slate-Delivery` (the attempt's row
id), the signature headers (`outbound.signed_headers`: `X-Slate-Timestamp`,
`X-Slate-Signature-V2` over `<timestamp>.<body>`, and the body-only `X-Slate-Signature`) and
`User-Agent: Slate/<version>` through `outbound.post` (one wall-clock deadline of
`webhook_timeout_seconds`, no redirects, connected to the address that was checked), and
records the outcome (`record_attempt`) which schedules any retry.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from slate import __version__, crypto, outbound
from slate import db as slate_db
from slate.models import Delivery, utcnow
from slate.service import deliveries, due, reminders
from slate.service.bus import bus
from slate.service.errors import Invalid
from slate.settings import settings

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]

# Tests swap in an `httpx.MockTransport`; None means real network.
transport: httpx.BaseTransport | None = None

# How often the loop prunes finished delivery rows (the first tick after a start prunes too).
PRUNE_EVERY = timedelta(hours=1)


def attempt(delivery_id: str, *, clock: Clock = utcnow, retry: bool = True) -> Delivery | None:
    """Send one claimed delivery and record the result. Blocking; run it in a worker thread."""
    with slate_db.SessionLocal() as session:
        job = deliveries.prepare_attempt(session, delivery_id, now=clock())
        if job is None:
            return session.get(Delivery, delivery_id)
    raw = job.body.encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Slate/{__version__}",
        "X-Slate-Event": job.event_type,
        "X-Slate-Delivery": job.delivery_id,
        **outbound.signed_headers(job.secret, raw),
    }
    status_code: int | None = None
    error: str | None = None
    started = time.perf_counter()
    final = False
    try:
        status_code = outbound.post(
            job.url, raw, headers, timeout=settings.webhook_timeout_seconds, transport=transport
        ).status
        if not 200 <= status_code < 300:
            error = f"HTTP {status_code}"
    except Invalid as exc:  # the target is refused (private address): no retry will help
        error, final = str(exc), True
    except Exception as exc:  # noqa: BLE001  # httpx errors, and e.g. IDNA errors before any I/O
        # Not only httpx.HTTPError: a hostname that passes validate_url but fails IDNA
        # encoding raised past here and left the row claimed as `sending` for ever.
        error = f"{type(exc).__name__}: {exc}"[:300]
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    with slate_db.SessionLocal() as session:
        return deliveries.record_attempt(
            session,
            delivery_id,
            status_code=status_code,
            response_ms=elapsed_ms,
            error=error,
            now=clock(),
            retry_delays=settings.webhook_retry_seconds,
            retry=retry and not final,
        )


class Dispatcher:
    def __init__(self, *, clock: Clock = utcnow, poll_seconds: float = 1.0) -> None:
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.cursor: str | None = None
        self._inflight: set[asyncio.Task] = set()
        self._pruned_at: datetime | None = None

    # Synchronous steps (each opens its own session); tests drive these directly.

    def start(self) -> None:
        """Begin after the newest event, and re-queue anything a crash left mid-send."""
        with slate_db.SessionLocal() as session:
            self.cursor = deliveries.latest_event_id(session)
            recovered = deliveries.recover_claimed(session)
        if recovered:
            log.info("re-queued %d webhook deliveries interrupted by a restart", recovered)

    def fan_out_once(self) -> int:
        with slate_db.SessionLocal() as session:
            self.cursor, queued = deliveries.fan_out(session, self.cursor, now=self.clock())
        return queued

    def claim(self, limit: int) -> list[str]:
        with slate_db.SessionLocal() as session:
            return deliveries.claim_due(session, now=self.clock(), limit=limit)

    def send_due_once(self) -> int:
        """Claim and send every due delivery, one after another. Returns how many were sent."""
        ids = self.claim(limit=10_000)
        for delivery_id in ids:
            attempt(delivery_id, clock=self.clock)
        return len(ids)

    def prune_once(self, limit: int = deliveries.BATCH) -> int:
        """Delete one batch of finished rows past the retention. Returns how many."""
        now = self.clock()
        self._pruned_at = now
        with slate_db.SessionLocal() as session:
            pruned = deliveries.prune(session, now=now, limit=limit)
        if pruned:
            log.info("pruned %d finished webhook deliveries", pruned)
        return pruned

    def _prune_due(self) -> bool:
        return self._pruned_at is None or self.clock() - self._pruned_at >= PRUNE_EVERY

    # The loop.

    async def run(self) -> None:
        wake = asyncio.Event()
        async with bus.subscribe() as sub:
            await asyncio.to_thread(self.start)

            async def doorbell() -> None:
                async for _ in sub:
                    wake.set()

            bell = asyncio.create_task(doorbell())
            try:
                while True:
                    try:
                        await asyncio.to_thread(self.fan_out_once)
                        free = settings.webhook_concurrency - len(self._inflight)
                        if free > 0:
                            for delivery_id in await asyncio.to_thread(self.claim, free):
                                task = asyncio.create_task(
                                    asyncio.to_thread(attempt, delivery_id, clock=self.clock)
                                )
                                self._inflight.add(task)
                                task.add_done_callback(self._done)
                        if self._prune_due():
                            await asyncio.to_thread(self.prune_once)
                    except Exception:
                        log.exception("webhook dispatcher step failed; retrying next tick")
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(wake.wait(), self.poll_seconds)
                    wake.clear()
            finally:
                bell.cancel()

    def _done(self, task: asyncio.Task) -> None:
        self._inflight.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("webhook delivery attempt crashed", exc_info=task.exception())


def start_dispatcher() -> asyncio.Task | None:
    """Start the dispatcher, or refuse (logged) when SLATE_SECRET_KEY is missing or weak."""
    try:
        crypto.require_key()
    except crypto.SecretKeyError as exc:
        log.error("webhooks disabled: %s", exc)
        return None
    return asyncio.create_task(Dispatcher().run(), name="slate-webhooks")


def due_events_tick(session: Session, now: datetime) -> int:
    """Scheduler hook: emit item.due_soon / item.due / item.overdue that have come due."""
    return due.emit_due_events(session, now=now)


def reminders_tick(session: Session, now: datetime) -> int:
    """Scheduler hook: record `item.reminder` for reminders that have come due."""
    return reminders.fire_reminders(session, now=now)


def digest_tick(session: Session, now: datetime) -> int:
    """Scheduler hook: each account's daily digest at its hour."""
    return reminders.send_digests(session, now=now)
