"""In-process event bus: the fan-out from committed changes to live consumers (SSE, webhooks).

`publish()` is synchronous and thread-safe, because service functions run in FastAPI's
threadpool. Each subscriber owns a bounded asyncio queue on its own loop; publish hands the
event over with `call_soon_threadsafe` and never waits, so no subscriber can stall a writer.
A subscriber that falls `max_queue` events behind loses the *oldest* queued events (logged,
counted in `dropped`). The bus keeps nothing; the events table is the durable record, so a
consumer that sees `dropped > 0` catches up from it with its last-delivered id as cursor
(the SSE stream does this itself; see `slate.api.events`).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

Message = dict[str, Any]


class Subscription:
    def __init__(self, bus: EventBus, max_queue: int) -> None:
        self._bus = bus
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=max_queue)
        self.dropped = 0  # events discarded since the consumer last reset this

    def _offer(self, message: Message) -> None:
        # Runs on the subscriber's loop, so it never races the consumer.
        if self._queue.full():
            self._queue.get_nowait()
            if self.dropped == 0:
                log.warning(
                    "event bus subscriber is %d events behind; dropping oldest",
                    self._queue.maxsize,
                )
            self.dropped += 1
        self._queue.put_nowait(message)

    def _deliver(self, message: Message) -> None:
        try:
            self._loop.call_soon_threadsafe(self._offer, message)
        except RuntimeError:  # loop closed underneath us
            self.close()

    def close(self) -> None:
        self._bus._remove(self)

    def drain(self) -> int:
        """Discard everything queued (a long-poll waiter only needs to know *something*
        happened). Returns how many messages were dropped."""
        n = 0
        while not self._queue.empty():
            self._queue.get_nowait()
            n += 1
        return n

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Message:
        return await self._queue.get()

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


class EventBus:
    def __init__(self, max_queue: int = 1000) -> None:
        self._max_queue = max_queue
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()
        # For synchronous waiters (MCP long-polls in a worker thread): a count of published
        # messages and a condition notified on each.
        self._seq = 0
        self._published = threading.Condition()

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def subscribe(self) -> Subscription:
        """Call from async code. Use as `async with bus.subscribe() as sub: async for m in sub`."""
        sub = Subscription(self, self._max_queue)
        with self._lock:
            self._subs.add(sub)
        return sub

    @property
    def seq(self) -> int:
        """How many messages have been published: pass to `wait_sync`."""
        return self._seq

    def publish(self, message: Message) -> None:
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            sub._deliver(message)
        with self._published:
            self._seq += 1
            self._published.notify_all()

    def wait_sync(self, seq: int, timeout: float) -> bool:
        """Block this (worker) thread until something is published after `seq`, at most
        `timeout` seconds. True if something was."""
        with self._published:
            return self._published.wait_for(lambda: self._seq != seq, timeout)

    def _remove(self, sub: Subscription) -> None:
        with self._lock:
            self._subs.discard(sub)


bus = EventBus()
