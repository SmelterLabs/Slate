"""The automations engine loop: one asyncio task, woken by the event bus.

Each step runs `service.automations.process()` in a worker thread: it reads every event after
its stored cursor, runs the rules they fire and moves the cursor, so a restart resumes where it
stopped. The bus subscription is only a doorbell (a change usually reaches its automation in
well under a second); a slow poll covers anything the doorbell misses. Events an automation
wrote ring the bell too, but never fire rules (see the service docstring).

The `webhook` action's signed HTTP POST happens here (`send`), outside any write transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

import httpx

from slate import __version__
from slate import db as slate_db
from slate.notify import sign
from slate.service import automations, webhooks
from slate.service.bus import bus

log = logging.getLogger(__name__)

RESPONSE_CAP_BYTES = 64 * 1024
TIMEOUT_SECONDS = 5.0
# Tests swap in an `httpx.MockTransport`; None means real network.
transport: httpx.BaseTransport | None = None


def send(url: str, payload: dict[str, Any], secret: str) -> tuple[bool, str]:
    """POST one automation webhook, signed with the rule's secret. One attempt; the result goes
    in the run log."""
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Slate/{__version__}",
        "X-Slate-Automation": payload["automation"]["id"],
        "X-Slate-Signature": sign(secret, body),
    }
    try:
        webhooks.check_target(url)
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with (
            httpx.Client(
                transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False
            ) as client,
            client.stream("POST", url, content=body, headers=headers) as response,
        ):
            # Read a bounded amount: the body is never kept (see notify.post_capped).
            got = 0
            for chunk in response.iter_bytes():
                got += len(chunk)
                if got >= RESPONSE_CAP_BYTES or time.monotonic() > deadline:
                    break
            status = response.status_code
    except Exception as exc:  # noqa: BLE001  # httpx errors and e.g. IDNA errors
        return False, f"{type(exc).__name__}: {exc}"[:300]
    return 200 <= status < 300, f"HTTP {status}"


def step() -> int:
    with slate_db.SessionLocal() as session:
        return automations.process(session, send=send)


async def run(poll_seconds: float = 5.0) -> None:
    wake = asyncio.Event()
    async with bus.subscribe() as sub:
        with slate_db.SessionLocal() as session:
            await asyncio.to_thread(automations.start, session)

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
                    log.exception("automations step failed; retrying")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), poll_seconds)
        finally:
            bell.cancel()


def start() -> asyncio.Task:
    return asyncio.create_task(run(), name="slate-automations")
