"""GET /api/v1/events/stream (SSE) and GET /api/v1/events (polling fallback).

SSE tests run against a real uvicorn server in a thread: httpx's ASGITransport and Starlette's
TestClient both wait for the whole response body, which never ends for a stream. The server
uses a file database (the shared in-memory one would put the test's and the server's sessions
on one connection, so one side's rollback would undo the other's writes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from slate import db as slate_db
from slate.api import events as events_api
from slate.app import create_app
from slate.db import Base, make_engine
from slate.models import Event, Source
from slate.service import accounts
from slate.service.bus import bus
from slate.service.core import Actor, apply_change

API = "/api/v1"


# --- fixtures ------------------------------------------------------------------------------


@dataclass
class Live:
    url: str
    Session: sessionmaker
    actor: Actor  # alex (admin)
    admin_token: str

    def token(self, handle: str, scopes=("read",)) -> str:
        with self.Session() as s:
            acct = accounts.create_account(
                s, self.actor, handle=handle, display_name=handle.title(), kind="agent"
            )
            _, plaintext = accounts.create_token(s, self.actor, account_id=acct.id, scopes=scopes)
        return plaintext

    def ping(self, n: int = 1, item_id: str | None = None, type_: str = "test.pinged") -> list:
        """Write `n` event-only changes through the service layer; return their ids."""
        ids = []
        with self.Session() as s:
            for i in range(n):
                with apply_change(s, self.actor) as cx:
                    ev = cx.record(type_, item_id=item_id, after={"n": i})
                ids.append(ev.id)
        return ids


@pytest.fixture
def live(tmp_path, monkeypatch) -> Live:
    engine = make_engine(f"sqlite+pysqlite:///{(tmp_path / 'live.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(slate_db, "SessionLocal", Session)

    with Session() as s:
        account, _, admin_token = accounts.bootstrap(s, handle="alex", display_name="Alex")
    actor = Actor(account_id=account.id, source=Source.api)

    config = uvicorn.Config(
        create_app(), host="127.0.0.1", port=0, log_config=None, timeout_graceful_shutdown=1
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    yield Live(f"http://127.0.0.1:{port}", Session, actor, admin_token)

    server.should_exit = True
    thread.join(timeout=5)
    engine.dispose()


@dataclass
class Msg:
    id: str | None
    event: str | None
    data: dict | None
    comment: str | None


async def _messages(resp: httpx.Response) -> AsyncIterator[Msg]:
    fields: dict[str, str] = {}
    comment = None
    async for line in resp.aiter_lines():
        if line == "":
            if fields or comment is not None:
                data = json.loads(fields["data"]) if "data" in fields else None
                yield Msg(fields.get("id"), fields.get("event"), data, comment)
            fields, comment = {}, None
        elif line.startswith(":"):
            comment = line[1:].strip()
        else:
            key, _, value = line.partition(":")
            fields[key] = value.removeprefix(" ")


class Stream:
    """An open SSE connection. `events()` skips comments; `ready()` waits for live mode."""

    def __init__(self, resp: httpx.Response) -> None:
        self.resp = resp
        self._it = _messages(resp)

    async def next(self, timeout: float = 2) -> Msg:
        return await asyncio.wait_for(anext(self._it), timeout)

    async def ready(self) -> list[Msg]:
        """Read until the `ready` comment; return the replayed events before it."""
        replayed = []
        while True:
            m = await self.next()
            if m.comment == "ready":
                return replayed
            if m.event:
                replayed.append(m)

    async def event(self, timeout: float = 2) -> Msg:
        while True:
            m = await self.next(timeout)
            if m.event:
                return m


async def _open(client: httpx.AsyncClient, stack, url: str, **kw) -> Stream:
    resp = await stack.enter_async_context(client.stream("GET", url, **kw))
    assert resp.status_code == 200, await resp.aread()
    assert resp.headers["content-type"].startswith("text/event-stream")
    return Stream(resp)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _wait_for(predicate, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)


# --- stream: live delivery -----------------------------------------------------------------


async def test_service_change_reaches_stream_within_one_second(live):
    reader = live.token("reader")
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        stream = await _open(client, stack, f"{API}/events/stream", headers=_bearer(reader))
        assert await stream.ready() == []

        start = time.monotonic()
        with live.Session() as s:
            acct = accounts.create_account(
                s, live.actor, handle="agent", display_name="Agent", kind="agent"
            )
        msg = await stream.event(timeout=1)
        assert time.monotonic() - start < 1

        with live.Session() as s:
            ev = s.scalar(select(Event).where(Event.subject_id == acct.id))
        assert msg.event == "account.created"
        assert msg.id == ev.id
        assert msg.data["id"] == ev.id
        assert msg.data["type"] == "account.created"
        assert msg.data["subject_id"] == acct.id
        assert msg.data["actor_id"] == live.actor.account_id


async def test_two_streams_both_see_a_third_partys_change(live):
    a, b = live.token("alpha"), live.token("bravo")
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        sa = await _open(client, stack, f"{API}/events/stream", headers=_bearer(a))
        sb = await _open(client, stack, f"{API}/events/stream", params={"token": b})
        await sa.ready()
        await sb.ready()

        # The third party writes through the REST API.
        start = time.monotonic()
        r = await client.post(
            f"{API}/accounts",
            json={"handle": "agent2", "display_name": "Agent 2", "kind": "agent"},
            headers=_bearer(live.admin_token),
        )
        assert r.status_code == 201
        ma, mb = await asyncio.gather(sa.event(timeout=1), sb.event(timeout=1))
        assert time.monotonic() - start < 1
        assert ma.event == mb.event == "account.created"
        assert ma.id == mb.id
        assert ma.data["subject_id"] == r.json()["id"]


async def test_type_and_item_id_filters(live):
    reader = live.token("reader")
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        by_type = await _open(
            client,
            stack,
            f"{API}/events/stream",
            params={"token": reader, "type": "test.b,test.c"},
        )
        by_item = await _open(
            client,
            stack,
            f"{API}/events/stream",
            params={"token": reader, "item_id": "item0000001"},
        )
        await by_type.ready()
        await by_item.ready()

        live.ping(type_="test.a", item_id="item0000001")
        b = live.ping(type_="test.b")[0]
        c = live.ping(type_="test.c", item_id="item0000002")[0]

        assert [(await by_type.event()).id for _ in range(2)] == [b, c]
        assert (await by_item.event()).event == "test.a"
        with pytest.raises(TimeoutError):
            await by_item.event(timeout=0.3)


# --- stream: catch-up ----------------------------------------------------------------------


async def test_since_replays_missed_events_once_then_continues_live(live):
    reader = live.token("reader")
    first, *missed = live.ping(4)
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        stream = await _open(
            client, stack, f"{API}/events/stream", params={"token": reader, "since": first}
        )
        replayed = await stream.ready()
        assert [m.id for m in replayed] == missed

        new = live.ping(2)
        assert [(await stream.event()).id for _ in range(2)] == new
        with pytest.raises(TimeoutError):
            await stream.event(timeout=0.3)


async def test_last_event_id_header_wins_over_since(live):
    """EventSource reconnects to the same URL (old `since`) plus the newer Last-Event-ID."""
    reader = live.token("reader")
    ids = live.ping(4)
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        stream = await _open(
            client,
            stack,
            f"{API}/events/stream",
            params={"token": reader, "since": ids[0]},
            headers={"Last-Event-ID": ids[2]},
        )
        assert [m.id for m in await stream.ready()] == [ids[3]]


async def test_catch_up_has_no_gap_or_duplicate_across_the_subscribe_replay_seam(live, monkeypatch):
    """Writes land after the stream subscribed but before its replay query (so they are in
    both the replay and the bus queue), and after the query (bus only). Each arrives once."""
    reader = live.token("reader")
    (start,) = live.ping(1)
    original = events_api._read_page
    calls = []

    def racing_read_page(since, filters, gate=None):
        calls.append(since)
        if len(calls) > 1:
            return original(since, filters, gate)
        live.ping(3)  # committed and published after subscribe, before the query
        page = original(since, filters, gate)
        live.ping(3)  # after the query: only the bus has these
        return page

    monkeypatch.setattr(events_api, "_read_page", racing_read_page)
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        stream = await _open(
            client, stack, f"{API}/events/stream", params={"token": reader, "since": start}
        )
        got = [m.id for m in await stream.ready()]
        assert len(got) == 3
        while len(got) < 6:
            got.append((await stream.event()).id)
        with pytest.raises(TimeoutError):
            await stream.event(timeout=0.3)

    with live.Session() as s:
        expected = list(s.scalars(select(Event.id).where(Event.id > start).order_by(Event.id)))
    assert got == expected
    assert len(expected) == 6


async def test_subscriber_that_overflows_resyncs_from_the_database(live, monkeypatch):
    monkeypatch.setattr(bus, "_max_queue", 2)
    reader = live.token("reader")
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        stream = await _open(client, stack, f"{API}/events/stream", params={"token": reader})
        await stream.ready()
        # One change, 30 events: all 30 publishes land on the server loop before the stream
        # coroutine runs again, so its 2-slot queue overflows.
        with live.Session() as s, apply_change(s, live.actor) as cx:
            ids = [cx.record("test.pinged", after={"n": n}) for n in range(30)]
        ids = [e.id for e in ids]
        got = [(await stream.event()).id for _ in range(30)]
        assert got == ids
        with pytest.raises(TimeoutError):
            await stream.event(timeout=0.3)


async def test_since_zero_replays_everything(live):
    reader = live.token("reader")
    with live.Session() as s:
        all_ids = list(s.scalars(select(Event.id).order_by(Event.id)))
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        stream = await _open(
            client, stack, f"{API}/events/stream", params={"token": reader, "since": "0"}
        )
        assert [m.id for m in await stream.ready()] == all_ids


# --- stream: connection lifecycle and auth -------------------------------------------------


async def test_disconnect_unsubscribes(live):
    reader = live.token("reader")
    base = bus.subscriber_count
    async with (
        httpx.AsyncClient(base_url=live.url) as client,
        client.stream("GET", f"{API}/events/stream", params={"token": reader}) as resp,
    ):
        await Stream(resp).ready()
        assert bus.subscriber_count == base + 1
    await _wait_for(lambda: bus.subscriber_count == base)


@pytest.mark.parametrize(
    ("params", "headers"),
    [
        ({}, {}),
        ({"token": "nope"}, {}),
        ({"token": "slate_AAAAAAAAAAAA_" + "x" * 43}, {}),
        ({}, {"Authorization": "Bearer nope"}),
        ({}, {"Authorization": "Basic abc"}),
    ],
)
async def test_stream_auth_failure_is_401_before_any_stream(live, params, headers):
    async with (
        httpx.AsyncClient(base_url=live.url) as client,
        client.stream("GET", f"{API}/events/stream", params=params, headers=headers) as resp,
    ):
        assert resp.status_code == 401
        assert not resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["WWW-Authenticate"] == "Bearer"


async def test_revoked_token_cannot_open_a_stream(live):
    reader = live.token("reader")
    token_id = reader.split("_")[1]
    async with httpx.AsyncClient(base_url=live.url) as client:
        r = await client.delete(f"{API}/tokens/{token_id}", headers=_bearer(reader))
        assert r.status_code == 204
        async with client.stream("GET", f"{API}/events/stream", params={"token": reader}) as resp:
            assert resp.status_code == 401


async def test_token_in_query_is_never_logged(live, caplog):
    reader = live.token("reader")
    caplog.set_level(logging.DEBUG)
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        stream = await _open(client, stack, f"{API}/events/stream", params={"token": reader})
        await stream.ready()
        async with client.stream("GET", f"{API}/events/stream", params={"token": "bad"}) as r:
            assert r.status_code == 401
    await asyncio.sleep(0.1)
    access = [r.getMessage() for r in caplog.records if r.name == "uvicorn.access"]
    assert any("/events/stream" in line for line in access)
    # httpx/httpcore are this test's own client logging the URL it sent; skip them.
    server = " ".join(
        r.getMessage() for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))
    )
    assert reader not in server
    assert "token=bad" not in server


def test_invalid_since_is_422(client):
    tok = client.post(f"{API}/bootstrap", json={"handle": "alex", "display_name": "Alex"})
    r = client.get(
        f"{API}/events/stream",
        params={"since": "not an id!"},
        headers=_bearer(tok.json()["token"]),
    )
    assert r.status_code == 422


# --- polling -------------------------------------------------------------------------------


@pytest.fixture
def admin(client) -> dict[str, str]:
    r = client.post(f"{API}/bootstrap", json={"handle": "alex", "display_name": "Alex"})
    return _bearer(r.json()["token"])


def _ping(db, n: int, type_: str = "test.pinged", item_id: str | None = None) -> list[str]:
    account = db.scalar(select(Event.actor_id).limit(1))
    actor = Actor(account_id=account, source=Source.api)
    out = []
    for i in range(n):
        with apply_change(db, actor) as cx:
            ev = cx.record(type_, item_id=item_id, after={"n": i})
        out.append(ev.id)  # assigned at flush, so read it after the block
    return out


def test_polling_pages_in_id_order(client, admin, db):
    all_ids = list(db.scalars(select(Event.id).order_by(Event.id)))  # bootstrap's two
    all_ids += _ping(db, 5)

    seen, since = [], None
    while True:
        params = {"limit": 3} | ({"since": since} if since else {})
        page = client.get(f"{API}/events", params=params, headers=admin).json()
        seen += [e["id"] for e in page["events"]]
        since = page["next_since"]
        if not page["has_more"]:
            break
    assert seen == all_ids

    empty = client.get(f"{API}/events", params={"since": since}, headers=admin).json()
    assert empty == {"events": [], "next_since": since, "has_more": False}


def test_polling_payload_matches_the_stream_shape(client, admin, db):
    (eid,) = _ping(db, 1, item_id="item0000001")
    ev = client.get(f"{API}/events", params={"item_id": "item0000001"}, headers=admin).json()
    assert ev["events"] == [
        {
            "id": eid,
            "type": "test.pinged",
            "item_id": "item0000001",
            "subject_id": "item0000001",
            "actor_id": ev["events"][0]["actor_id"],
            "source": "api",
            "field": None,
            "before": None,
            "after": {"n": 0},
            "session_ref": None,
            "at": ev["events"][0]["at"],
        }
    ]


def test_polling_filters(client, admin, db):
    a = _ping(db, 2, type_="test.a")
    b = _ping(db, 1, type_="test.b", item_id="item0000001")
    _ping(db, 1, type_="test.c")

    def ids(**params):
        r = client.get(f"{API}/events", params=params, headers=admin)
        return [e["id"] for e in r.json()["events"]]

    assert ids(type="test.a,test.b") == a + b
    assert ids(item_id="item0000001") == b
    assert ids(type="test.a", since=a[0]) == a[1:]


@pytest.mark.parametrize("limit", [0, 1001])
def test_polling_limit_bounds(client, admin, limit):
    assert client.get(f"{API}/events", params={"limit": limit}, headers=admin).status_code == 422


def test_polling_requires_read_token(client, admin):
    assert client.get(f"{API}/events").status_code == 401
    assert client.get(f"{API}/events", headers=_bearer("nope")).status_code == 401


def test_event_endpoints_are_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert f"{API}/events" in paths
    assert f"{API}/events/stream" in paths
