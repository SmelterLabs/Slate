"""Database connections: a request that waits never holds one, and the pool never runs dry.

The token check runs on the event loop. If it ever had to wait for a pooled connection, the
loop would stop, and with it every request that would have handed one back: the whole server
would freeze until the pool's timeout. These tests run a real uvicorn server on a file
database, as in production, keep many requests waiting (long-polls, live streams, uploads
whose body trickles in), and check that an ordinary request still answers at once.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from sqlalchemy.orm import sessionmaker

from slate import db as slate_db
from slate.app import create_app
from slate.db import Base, make_engine
from slate.service import accounts

API = "/api/v1"
WAITERS = 40  # well above the 15 connections a default pool would allow


@pytest.fixture
def server(tmp_path, monkeypatch) -> Iterator[tuple[str, dict, object]]:
    engine = make_engine(f"sqlite+pysqlite:///{(tmp_path / 'pool.db').as_posix()}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(slate_db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    with slate_db.SessionLocal() as s:
        _, _, token = accounts.bootstrap(s, handle="alex", display_name="Alex")
    config = uvicorn.Config(
        create_app(), host="127.0.0.1", port=0, log_config=None, timeout_graceful_shutdown=1
    )
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", {"Authorization": f"Bearer {token}"}, engine
    srv.should_exit = True
    thread.join(timeout=5)
    engine.dispose()


def test_file_pool_never_makes_a_caller_wait(tmp_path):
    engine = make_engine(f"sqlite+pysqlite:///{(tmp_path / 'p.db').as_posix()}")
    try:
        held = [engine.raw_connection() for _ in range(WAITERS)]
        assert len(held) == WAITERS  # a bounded pool would have timed out here
        for conn in held:
            conn.close()
    finally:
        engine.dispose()


def _item(base: str, h: dict) -> str:
    sec = httpx.post(f"{base}{API}/sections", json={"name": "P", "audience": "personal"}, headers=h)
    lst = httpx.post(
        f"{base}{API}/lists", json={"section_id": sec.json()["id"], "name": "T"}, headers=h
    )
    item = httpx.post(
        f"{base}{API}/items", json={"list_id": lst.json()["id"], "title": "x"}, headers=h
    )
    return item.json()["id"]


def test_waiting_requests_do_not_hold_connections(server):
    base, h, engine = server
    item_id = _item(base, h)
    since = httpx.get(f"{base}{API}/checkin", headers=h).json()["next_since"]
    stop = threading.Event()

    def trickle() -> Iterator[bytes]:
        for _ in range(40):
            if stop.is_set():
                return
            yield b"x"
            time.sleep(0.1)

    def upload() -> None:
        with contextlib.suppress(httpx.HTTPError):
            httpx.post(
                f"{base}{API}/items/{item_id}/attachments?filename=a.txt",
                content=trickle(),
                headers={**h, "Content-Type": "text/plain"},
                timeout=20,
            )

    def long_poll() -> None:
        with contextlib.suppress(httpx.HTTPError):
            httpx.get(
                f"{base}{API}/checkin",
                params={"since": since, "wait": 4},
                headers=h,
                timeout=20,
            )

    def stream() -> None:
        try:
            with httpx.stream("GET", f"{base}{API}/events/stream", headers=h, timeout=20) as r:
                for _ in r.iter_bytes():
                    if stop.is_set():
                        return
        except httpx.HTTPError:
            pass

    threads = [
        threading.Thread(target=f) for f in (upload, long_poll, stream) for _ in range(WAITERS // 2)
    ]
    for t in threads:
        t.start()
    try:
        time.sleep(1.0)  # every waiter is in: bodies trickling, polls and streams waiting
        assert engine.pool.checkedout() <= 5, engine.pool.status()
        started = time.perf_counter()
        r = httpx.get(f"{base}{API}/favorites", headers=h, timeout=10)
        elapsed = time.perf_counter() - started
        assert r.status_code == 200, r.text
        assert elapsed < 2.0, f"an ordinary request took {elapsed:.1f} s behind waiting ones"
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=20)
