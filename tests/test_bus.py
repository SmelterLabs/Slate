"""In-process event bus: sync publish, async-iterator subscribe."""

from __future__ import annotations

import asyncio
import threading

from slate.service.bus import EventBus


async def test_subscriber_receives_published_events_in_order():
    bus = EventBus()
    async with bus.subscribe() as sub:
        bus.publish({"n": 1})
        bus.publish({"n": 2})
        got = [await asyncio.wait_for(anext(sub), 1), await asyncio.wait_for(anext(sub), 1)]
    assert got == [{"n": 1}, {"n": 2}]


async def test_every_subscriber_gets_every_event():
    bus = EventBus()
    async with bus.subscribe() as a, bus.subscribe() as b:
        bus.publish({"n": 1})
        assert await asyncio.wait_for(anext(a), 1) == {"n": 1}
        assert await asyncio.wait_for(anext(b), 1) == {"n": 1}


async def test_publish_from_a_worker_thread_reaches_the_loop():
    """Sync FastAPI handlers run in a threadpool; publish must be thread-safe."""
    bus = EventBus()
    async with bus.subscribe() as sub:
        t = threading.Thread(target=bus.publish, args=({"from": "thread"},))
        t.start()
        t.join()
        assert await asyncio.wait_for(anext(sub), 1) == {"from": "thread"}


async def test_closed_subscription_is_removed():
    bus = EventBus()
    async with bus.subscribe():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0
    bus.publish({"n": 1})  # no subscribers: a no-op, not an error


async def test_full_queue_drops_oldest_and_counts_it(caplog):
    bus = EventBus(max_queue=2)
    async with bus.subscribe() as sub:
        for n in range(5):
            bus.publish({"n": n})
        await asyncio.sleep(0)
        assert sub.dropped == 3
        got = [await asyncio.wait_for(anext(sub), 1) for _ in range(2)]
    assert got == [{"n": 3}, {"n": 4}]
    assert "dropping oldest" in caplog.text


async def test_unconsumed_subscriber_never_blocks_publish():
    bus = EventBus(max_queue=1)
    async with bus.subscribe():
        t = threading.Thread(target=lambda: [bus.publish({"n": n}) for n in range(10_000)])
        t.start()
        t.join(timeout=5)
        assert not t.is_alive()


def test_publish_without_any_loop_is_safe():
    EventBus().publish({"n": 1})
