"""Webhook delivery: fan-out, signing, payload, filters, retries, restart, disabled hooks.

The receiver is an `httpx.MockTransport` swapped into `slate.notify`; the clock is a fake the
test moves by hand, so the retry schedule is checked to the second without waiting.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from slate import notify
from slate.models import Delivery, Source
from slate.service import accounts, items, lists, sections, webhooks
from slate.service.core import Actor, apply_change
from slate.settings import settings

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
FLAT_KEYS = {
    "id",
    "type",
    "item_id",
    "subject_id",
    "actor_id",
    "source",
    "field",
    "before",
    "after",
    "session_ref",
    "at",
}


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class Receiver:
    """Records requests; answers with the queued statuses (last one repeats)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.statuses: list[int | Exception] = [200]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, text="nope" if answer >= 400 else "ok")

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def receiver(monkeypatch) -> Receiver:
    rx = Receiver()
    monkeypatch.setattr(notify, "transport", httpx.MockTransport(rx))
    return rx


@pytest.fixture
def world(db):
    alex, _, alex_tok = accounts.bootstrap(db, handle="alex", display_name="Alex")
    admin = Actor(account_id=alex.id, source=Source.api, session_ref="s-1")
    agent = accounts.create_account(db, admin, handle="agent", display_name="Agent", kind="agent")
    section = sections.create_section(db, admin, name="Personal", audience="personal")
    todo = lists.create_list(db, admin, section_id=section.id, name="Todo")
    other = lists.create_list(db, admin, section_id=section.id, name="Later")
    return {
        "admin": admin,
        "alex_by": accounts.resolve_token(db, alex_tok),
        "agent": Actor(account_id=agent.id, source=Source.api),
        "section": section,
        "todo": todo,
        "other": other,
    }


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def dispatcher(clock) -> notify.Dispatcher:
    d = notify.Dispatcher(clock=clock)
    d.start()
    return d


def _hook(db, actor, events=("*",), **filter):
    return webhooks.create_webhook(
        db, actor, url="https://rx.example.com/slate", events=list(events), filter=filter
    )


def _rows(db, hook_id) -> list[Delivery]:
    db.expire_all()
    return list(
        db.scalars(
            select(Delivery).where(Delivery.webhook_id == hook_id).order_by(Delivery.attempt)
        )
    )


def test_signed_delivery_with_flat_payload_item_and_actor(db, world, receiver, dispatcher):
    hook, secret = _hook(db, world["agent"], events=["item.created"])
    item = items.create_item(
        db,
        world["admin"],
        list_id=world["todo"].id,
        title="Take out trash",
        priority="high",
        due_at="2026-10-28",
        tags=["chores"],
        assignees=["agent"],
    )
    assert dispatcher.fan_out_once() == 1
    assert dispatcher.send_due_once() == 1

    [req] = receiver.requests
    expected = "sha256=" + hmac.new(secret.encode(), req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-Slate-Signature"] == expected
    assert req.headers["X-Slate-Event"] == "item.created"
    assert req.headers["User-Agent"].startswith("Slate/")
    assert req.headers["Content-Type"] == "application/json"
    [row] = _rows(db, hook.id)
    assert req.headers["X-Slate-Delivery"] == row.id
    assert secret not in req.content.decode() and secret not in str(req.headers)

    body = json.loads(req.content)
    assert set(body) == FLAT_KEYS | {"item", "actor"}
    assert body["type"] == "item.created" and body["item_id"] == item.id
    assert body["source"] == "api" and body["session_ref"] == "s-1"
    assert body["actor"] == {
        "id": world["admin"].account_id,
        "handle": "alex",
        "kind": "person",
    }
    assert body["item"] == {
        "id": item.id,
        "title": "Take out trash",
        "list_id": world["todo"].id,
        "status": "To do",
        "priority": "high",
        "due_at": "2026-10-28T22:00:00.000000Z",
        "assignees": ["agent"],
        "tags": ["chores"],
    }
    assert (row.state, row.status_code, row.attempt) == ("ok", 200, 1)
    assert row.response_ms is not None and row.error is None


def test_type_matching_and_star_excludes_webhook_events(db, world, receiver, dispatcher):
    star, _ = _hook(db, world["agent"])
    due_only, _ = _hook(db, world["agent"], events=["item.due"])
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="x")
    dispatcher.fan_out_once()
    assert [r.event_id for r in _rows(db, due_only.id)] == []
    star_types = {b["type"] for b in _deliver(dispatcher, receiver)}
    assert "item.created" in star_types
    assert not any(t.startswith("webhook.") for t in star_types)  # its own created event


def _deliver(dispatcher, receiver) -> list[dict]:
    receiver.requests.clear()
    dispatcher.fan_out_once()
    dispatcher.send_due_once()
    return receiver.bodies()


def test_assignee_me_filter(db, world, receiver, dispatcher):
    _hook(db, world["agent"], events=["item.created", "item.assigned"], assignee_me=True)
    mine = items.create_item(
        db, world["admin"], list_id=world["todo"].id, title="mine", assignees=["agent"]
    )
    items.create_item(
        db, world["admin"], list_id=world["todo"].id, title="alex's", assignees=["alex"]
    )
    got = _deliver(dispatcher, receiver)
    assert sorted((b["type"], b["item_id"]) for b in got) == [
        ("item.assigned", mine.id),
        ("item.created", mine.id),
    ]

    # Being taken off an item is news to the one taken off.
    items.update_item(db, world["admin"], mine.id, {"assignees": ["alex"]})
    got = _deliver(dispatcher, receiver)
    assert [(b["type"], b["before"], b["after"]) for b in got] == [
        ("item.assigned", ["agent"], ["alex"])
    ]


def test_assignee_me_also_hears_mentions_and_watched_items(db, world, receiver, dispatcher):
    from slate.service import comments, watchers

    _hook(
        db,
        world["agent"],
        events=["item.commented", "item.mentioned", "item.updated"],
        assignee_me=True,
    )
    item = items.create_item(db, world["admin"], list_id=world["todo"].id, title="alex's")
    comments.add_comment(db, world["admin"], item.id, body_md="no mention here")
    mention = comments.add_comment(db, world["admin"], item.id, body_md="@agent can you look?")
    got = _deliver(dispatcher, receiver)
    assert [(b["type"], b["after"]["mentions"]) for b in got] == [("item.commented", ["agent"])]

    # A mention added by an edit is delivered as item.mentioned; the rest of the edit is not.
    plain = comments.add_comment(db, world["admin"], item.id, body_md="later")
    comments.edit_comment(db, world["admin"], plain.id, body_md="later, @agent")
    comments.edit_comment(db, world["admin"], mention.id, body_md="@agent can you look now?")
    got = _deliver(dispatcher, receiver)
    assert [(b["type"], b["after"]) for b in got] == [
        ("item.mentioned", {"comment_id": plain.id, "mentions": ["agent"]})
    ]

    # Watching makes every change on the item "about me".
    watchers.watch(db, world["agent"], item.id)
    items.update_item(db, world["admin"], item.id, {"title": "renamed"})
    got = _deliver(dispatcher, receiver)
    assert [(b["type"], b["after"]) for b in got] == [("item.updated", "renamed")]


def test_list_section_and_tag_filters(db, world, receiver, dispatcher):
    by_list, _ = _hook(db, world["agent"], events=["item.created"], list_ids=[world["other"].id])
    by_tag, _ = _hook(db, world["agent"], events=["item.created"], tags=["H3"])
    by_section, _ = _hook(
        db, world["agent"], events=["item.created"], section_ids=[world["section"].id]
    )
    a = items.create_item(db, world["admin"], list_id=world["todo"].id, title="a", tags=["h3"])
    b = items.create_item(db, world["admin"], list_id=world["other"].id, title="b")
    dispatcher.fan_out_once()
    assert {r.event_id for r in _rows(db, by_list.id)} == {_created_event(db, b.id)}
    assert {r.event_id for r in _rows(db, by_tag.id)} == {_created_event(db, a.id)}
    assert len(_rows(db, by_section.id)) == 2


def test_item_filters_skip_events_without_an_item(db, world, receiver, dispatcher):
    hook, _ = _hook(db, world["agent"], events=["section.created"], tags=["h3"])
    plain, _ = _hook(db, world["agent"], events=["section.created"])
    sections.create_section(db, world["admin"], name="AI", audience="ai")
    dispatcher.fan_out_once()
    assert _rows(db, hook.id) == []
    assert len(_rows(db, plain.id)) == 1


def _created_event(db, item_id):
    from slate.models import Event

    return db.scalar(select(Event.id).where(Event.item_id == item_id, Event.type == "item.created"))


def test_retry_schedule_then_final_failure(db, world, receiver, dispatcher, clock):
    hook, _ = _hook(db, world["agent"], events=["item.created"])
    receiver.statuses = [503]
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="x")
    dispatcher.fan_out_once()
    delays = settings.webhook_retry_seconds
    assert delays == [10, 60, 300, 1800, 7200]

    assert dispatcher.send_due_once() == 1
    for delay in delays:
        clock.advance(seconds=delay - 1)
        assert dispatcher.send_due_once() == 0, "sent before its backoff elapsed"
        clock.advance(seconds=1)
        assert dispatcher.send_due_once() == 1

    rows = _rows(db, hook.id)
    assert [r.attempt for r in rows] == [1, 2, 3, 4, 5, 6]
    assert [r.state for r in rows] == ["retry"] * 5 + ["failed"]
    assert all(r.status_code == 503 and r.error.startswith("HTTP 503") for r in rows)
    sent_at = [r.at for r in rows]
    assert [(b - a).total_seconds() for a, b in zip(sent_at, sent_at[1:], strict=False)] == delays
    assert len(receiver.requests) == 6
    assert len({r.content for r in receiver.requests}) == 1, "every retry sends identical bytes"
    clock.advance(days=1)
    assert dispatcher.send_due_once() == 0


def test_retry_delays_are_configurable(db, world, receiver, dispatcher, clock, monkeypatch):
    monkeypatch.setattr(settings, "webhook_retry_seconds", [0.001, 0.002])
    hook, _ = _hook(db, world["agent"], events=["item.created"])
    receiver.statuses = [500]
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="x")
    dispatcher.fan_out_once()
    for _ in range(3):
        dispatcher.send_due_once()
        clock.advance(seconds=1)
    assert [r.state for r in _rows(db, hook.id)] == ["retry", "retry", "failed"]


@pytest.mark.parametrize("code", [400, 401, 404, 410, 422])
def test_4xx_is_final(db, world, receiver, dispatcher, clock, code):
    hook, _ = _hook(db, world["agent"], events=["item.created"])
    receiver.statuses = [code]
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="x")
    dispatcher.fan_out_once()
    dispatcher.send_due_once()
    clock.advance(hours=5)
    assert dispatcher.send_due_once() == 0
    [row] = _rows(db, hook.id)
    assert (row.state, row.status_code) == ("failed", code)


def test_429_and_network_errors_retry(db, world, receiver, dispatcher, clock):
    hook, _ = _hook(db, world["agent"], events=["item.created"])
    receiver.statuses = [429, httpx.ConnectError("refused"), 200]
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="x")
    dispatcher.fan_out_once()
    for _ in range(3):
        dispatcher.send_due_once()
        clock.advance(hours=1)
    rows = _rows(db, hook.id)
    assert [(r.state, r.status_code) for r in rows] == [
        ("retry", 429),
        ("retry", None),
        ("ok", 200),
    ]
    assert rows[1].error.startswith("ConnectError")


def test_restart_resumes_pending_and_interrupted_deliveries(db, world, receiver, clock):
    hook, _ = _hook(db, world["agent"], events=["item.created"])
    first = notify.Dispatcher(clock=clock)
    first.start()
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="a")
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="b")
    first.fan_out_once()
    first.claim(limit=1)  # claimed, then the process dies before sending
    del first

    second = notify.Dispatcher(clock=clock)
    second.start()
    assert second.send_due_once() == 2
    assert {b["item"]["title"] for b in receiver.bodies()} == {"a", "b"}
    assert [r.state for r in _rows(db, hook.id)] == ["ok", "ok"]
    # nothing is sent twice after a further restart
    third = notify.Dispatcher(clock=clock)
    third.start()
    third.fan_out_once()
    assert third.send_due_once() == 0


def test_body_is_stored_once_per_event_and_finished_rows_are_pruned(
    db, world, receiver, dispatcher, clock, monkeypatch
):
    """A retry row hands its signed body to the next attempt instead of copying it, and rows
    that are finished (ok, failed, skipped, retry) older than the retention are pruned from the
    dispatcher loop, bounded per tick. Queued rows and younger history stay."""
    hook, _ = _hook(db, world["agent"], events=["item.created"])
    receiver.statuses = [503, 200]
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="a")
    dispatcher.fan_out_once()
    dispatcher.send_due_once()
    clock.advance(seconds=10)
    dispatcher.send_due_once()
    rows = _rows(db, hook.id)
    assert [r.state for r in rows] == ["retry", "ok"]
    assert rows[0].body == "" and json.loads(rows[1].body)["item"]["title"] == "a"
    assert receiver.requests[0].content == receiver.requests[1].content
    clock.advance(days=20)
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="b")
    dispatcher.fan_out_once()  # queued, never sent: must survive any prune
    clock.advance(days=11)
    assert dispatcher.prune_once(limit=1) == 1
    assert dispatcher.prune_once() == 1
    assert [(r.state, json.loads(r.body)["item"]["title"]) for r in _rows(db, hook.id)] == [
        ("pending", "b")
    ]
    assert dispatcher.prune_once() == 0
    # The Health page still counts the queue; a disabled retention keeps everything.
    receiver.statuses = [200]
    dispatcher.send_due_once()
    clock.advance(days=40)
    monkeypatch.setattr(settings, "delivery_retention_days", 0)
    assert dispatcher.prune_once() == 0
    assert [r.state for r in _rows(db, hook.id)] == ["ok"]


def test_disabled_subscription_receives_nothing(db, world, receiver, dispatcher):
    agent_by = world["alex_by"]  # admin may manage any hook
    off, _ = _hook(db, world["agent"], events=["item.created"])
    webhooks.update_webhook(db, world["admin"], off.id, {"disabled": True}, by=agent_by)
    later, _ = _hook(db, world["agent"], events=["item.created"])
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="x")
    dispatcher.fan_out_once()
    webhooks.update_webhook(  # queued, then disabled
        db, world["admin"], later.id, {"disabled": True}, by=agent_by
    )
    dispatcher.send_due_once()
    assert receiver.requests == []
    assert _rows(db, off.id) == []
    assert [r.state for r in _rows(db, later.id)] == ["skipped"]


def test_due_soon_goes_only_to_subscriptions_with_that_lead(db, world, receiver, dispatcher):
    h24, _ = _hook(db, world["agent"], events=["item.due_soon"])
    h2, _ = _hook(db, world["agent"], events=["item.due_soon"], due_lead_hours=2)
    item = items.create_item(db, world["admin"], list_id=world["todo"].id, title="x")
    with apply_change(db, world["admin"]) as cx:
        cx.record("item.due_soon", item_id=item.id, field="due_at", after={"lead_hours": 2})
    dispatcher.fan_out_once()
    assert _rows(db, h24.id) == []
    assert len(_rows(db, h2.id)) == 1


def test_undecryptable_secret_fails_cleanly(db, world, receiver, dispatcher, monkeypatch):
    hook, _ = _hook(db, world["agent"], events=["item.created"])
    items.create_item(db, world["admin"], list_id=world["todo"].id, title="x")
    dispatcher.fan_out_once()
    monkeypatch.setattr(settings, "secret_key", "cd" * 32)
    dispatcher.send_due_once()
    [row] = _rows(db, hook.id)
    assert row.state == "failed" and "SLATE_SECRET_KEY" in row.error
    assert receiver.requests == []


async def test_running_dispatcher_delivers_within_a_second(tmp_path, monkeypatch, receiver):
    """The real loop, on a file database (worker threads need their own connections)."""
    import asyncio

    from sqlalchemy.orm import sessionmaker

    from slate import db as slate_db
    from slate.db import Base, make_engine

    engine = make_engine(f"sqlite+pysqlite:///{(tmp_path / 'wh.db').as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(slate_db, "SessionLocal", Session)
    with Session() as s:
        alex, _, _ = accounts.bootstrap(s, handle="alex", display_name="Alex")
        actor = Actor(account_id=alex.id, source=Source.api)
        _hook(s, actor, events=["section.created"])

    task = asyncio.create_task(notify.Dispatcher(poll_seconds=5).run())
    try:
        await asyncio.sleep(0.2)  # let it subscribe and take its cursor
        with Session() as s:
            await asyncio.to_thread(sections.create_section, s, actor, name="Work", audience="work")
        for _ in range(100):
            if receiver.requests:
                break
            await asyncio.sleep(0.01)
        assert [b["type"] for b in receiver.bodies()] == ["section.created"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        engine.dispose()
