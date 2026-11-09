"""The bus cannot stall writers, and events commit and publish in id order.

Gap-free SSE catch-up (slate.api.events) relies on the second property: if event ids are
assigned, committed and published in the same order, "everything after id X" is a complete
answer whether it is read from the table or from the bus.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from slate.db import Base, make_engine
from slate.models import Source
from slate.service import accounts
from slate.service.bus import bus
from slate.service.core import Actor, apply_change


def _alex(session) -> Actor:
    account, _, _ = accounts.bootstrap(session, handle="alex", display_name="Alex")
    return Actor(account_id=account.id, source=Source.api)


async def test_slow_subscriber_cannot_stall_apply_change(db, monkeypatch):
    monkeypatch.setattr(bus, "_max_queue", 2)
    async with bus.subscribe() as sub:
        # This coroutine never reads `sub`, and while the writes below run the loop is blocked
        # outright: the worst possible consumer.
        actor = _alex(db)
        start = time.monotonic()
        for n in range(50):
            accounts.create_account(
                db, actor, handle=f"agent{n}", display_name=f"Agent {n}", kind="agent"
            )
        assert time.monotonic() - start < 5
        await asyncio.sleep(0)
        assert sub.dropped > 0
    assert bus.subscriber_count == 0


async def test_concurrent_writers_publish_in_id_order(tmp_path):
    engine = make_engine(f"sqlite+pysqlite:///{(tmp_path / 'order.db').as_posix()}")
    Base.metadata.create_all(engine)

    @event.listens_for(engine, "before_cursor_execute")
    def widen_the_race(_conn, _cursor, statement, *_args) -> None:  # noqa: ANN001
        # The event id is already chosen here; stall before it reaches SQLite's write lock.
        if statement.startswith("INSERT INTO events"):
            time.sleep(random.random() * 0.003)

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        actor = _alex(s)

    def writer(w: int) -> None:
        # Event-only changes (like the scheduler's `item.due`): the events INSERT is the
        # transaction's first write, so nothing else has taken SQLite's write lock yet.
        with Session() as s:
            for n in range(15):
                with apply_change(s, actor) as cx:
                    cx.record("test.pinged", after={"writer": w, "n": n})

    async with bus.subscribe() as sub:
        threads = [threading.Thread(target=writer, args=(w,)) for w in range(6)]
        for t in threads:
            t.start()
        await asyncio.to_thread(lambda: [t.join() for t in threads])
        await asyncio.sleep(0.05)
        published = []
        while not sub._queue.empty():
            published.append((await anext(sub))["id"])
    engine.dispose()
    assert len(published) == 90
    assert published == sorted(published)
