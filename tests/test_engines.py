"""The event-log engines: one loop for all four, failures shown on Health and alerted, one bad
event or delivery skipped instead of stopping everything after it, and an automation never run
twice for one event."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from slate import automator, emailer, engine, monitor, notify, pusher
from slate.db import SessionLocal
from slate.models import AutomationRun, Delivery, EmailDelivery, Event, PushDelivery, utcnow
from slate.service import automations, deliveries, email, push
from slate.settings import settings

API = "/api/v1"


@pytest.fixture(autouse=True)
def fresh_monitor():
    monitor.engines.reset()
    monitor.tasks.clear()
    yield
    monitor.engines.reset()
    monitor.tasks.clear()


def _events(kind: str) -> list[Event]:
    with SessionLocal() as s:
        return list(s.scalars(select(Event).where(Event.type == kind)))


def _item(client, headers, todo, title="Fix gutter"):
    r = client.post(f"{API}/items", json={"list_id": todo["id"], "title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- one loop, reported to Health ------------------------------------------------------------


async def test_every_engine_runs_the_shared_loop(monkeypatch):
    seen = []

    async def fake_run(name, step, *, poll_seconds, start=None):
        seen.append(name)

    monkeypatch.setattr(engine, "run", fake_run)
    await automator.run()
    await pusher.run()
    await emailer.run()
    await notify.Dispatcher().run()
    assert seen == ["automations", "push", "email", "webhooks"]


async def test_a_failing_engine_step_shows_on_health_and_alerts(client, alex, monkeypatch):
    monkeypatch.setattr(settings, "health_alert_after", 2)
    fail = {"on": True}

    def step():
        if fail["on"]:
            raise RuntimeError("smtp went away")

    for _ in range(3):
        await engine.step_once("email", step)
    loop_task = asyncio.get_running_loop().create_future()  # stands in for a running task
    monitor.track("email", loop_task)
    body = client.get(f"{API}/health", headers=alex).json()
    line = next(e for e in body["engines"] if e["key"] == "email")
    assert line["status"] == "fail" and "smtp went away" in line["detail"]
    (alert,) = _events("health.alert")
    assert alert.after_json["check"] == "engine:email"
    assert alert.after_json["label"] == "Email notifications"
    assert alert.after_json["failures"] == 2

    fail["on"] = False
    assert await engine.step_once("email", step) is None
    (rec,) = _events("health.recovered")
    assert rec.after_json["check"] == "engine:email"
    body = client.get(f"{API}/health", headers=alex).json()
    assert next(e for e in body["engines"] if e["key"] == "email")["status"] == "ok"
    loop_task.cancel()


def test_health_counts_push_and_email_waiting(client, alex, db, todo):
    now = utcnow()
    me = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    _item(client, alex, todo)
    ev = db.scalar(select(Event).order_by(Event.id.desc()).limit(1))
    db.add(
        EmailDelivery(account_id=me, event_id=ev.id, send_after=now, expires_at=now + timedelta(1))
    )
    db.add(
        PushDelivery(
            subscription_id="sub-1",
            event_id=ev.id,
            account_id=me,
            next_attempt_at=now,
            expires_at=now + timedelta(1),
        )
    )
    db.commit()
    queues = client.get(f"{API}/health", headers=alex).json()["queues"]
    assert queues["email_pending"] == 1 and queues["push_pending"] == 1


# --- one bad event or row is skipped, the rest go on ----------------------------------------


def test_a_webhook_event_that_cannot_be_summarised_does_not_stop_the_rest(
    client, alex, todo, db, monkeypatch
):
    from slate.service import webhooks
    from slate.service.core import Actor

    me = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    webhooks.create_webhook(
        db, Actor(account_id=me, source="api"), url="https://rx.example.com/h", events=["*"]
    )
    cursor = deliveries.latest_event_id(db)
    bad = _item(client, alex, todo, "Bad")
    _item(client, alex, todo, "Good")
    real = deliveries.delivery_body

    def body(session, ev, reader=None):
        if ev.item_id == bad["id"]:
            raise KeyError("broken summary")
        return real(session, ev, reader)

    monkeypatch.setattr(deliveries, "delivery_body", body)
    new_cursor, queued = deliveries.fan_out(db, cursor, now=utcnow())
    assert queued >= 1 and new_cursor == deliveries.latest_event_id(db)
    queued_for = {db.get(Event, d.event_id).item_id for d in db.scalars(select(Delivery))}
    assert bad["id"] not in queued_for and len(queued_for) == 1


def test_a_push_that_cannot_be_built_fails_alone(client, alex, todo, db, make_account, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec

    from slate import webpush

    _, author = make_account("my-agent")
    for n in range(2):
        key = ec.generate_private_key(ec.SECP256R1())
        body = {
            "endpoint": f"https://push.example.com/send/{n}",
            "keys": {
                "p256dh": webpush.b64url(webpush._raw_public(key.public_key())),
                "auth": webpush.b64url(b"0123456789abcdef"),
            },
        }
        r = client.post(f"{API}/push/subscriptions", headers=alex, json=body)
        assert r.status_code in (200, 201), r.text
    sent = []
    push.process(db, lambda *a: sent.append(a) or (201, "", None))
    task = _item(client, author, todo)
    r = client.post(f"{API}/items/{task['id']}/comments", json={"body_md": "@alex"}, headers=author)
    assert r.status_code == 201
    real = push.notification_for
    calls = {"n": 0}

    def note(session, ev, reason):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("cannot render")
        return real(session, ev, reason)

    monkeypatch.setattr(push, "notification_for", note)
    assert push.process(db, lambda *a: sent.append(a) or (201, "", None)) == 1
    states = sorted(r.state for r in db.scalars(select(PushDelivery)))
    assert states == ["failed", "sent"]


def test_an_email_that_cannot_be_rendered_fails_alone(
    client, alex, todo, db, make_account, monkeypatch
):
    client.patch(f"{API}/me/prefs", json={"email_to": "alex@example.com"}, headers=alex)
    sent = []
    send = lambda to, subject, text, html: sent.append(text)  # noqa: E731
    email.process(db, send)
    _, agent = make_account("my-agent")
    first = _item(client, agent, todo, "First")
    second = _item(client, agent, todo, "Second")
    for it in (first, second):
        client.post(f"{API}/items/{it['id']}/comments", json={"body_md": "@alex"}, headers=agent)
    email.process(db, send)
    real = email._render

    def render(session, account, ev, reason):
        if ev.item_id == first["id"]:
            raise ValueError("cannot render")
        return real(session, account, ev, reason)

    monkeypatch.setattr(email, "_render", render)
    assert email.process(db, send, now=utcnow() + timedelta(minutes=6)) == 1
    assert "Second" in sent[0] and "First" not in sent[0]
    states = sorted(r.state for r in db.scalars(select(EmailDelivery)))
    assert states == ["failed", "sent"]


# --- automations: an event that fails is skipped; a rule never runs twice for one event ------


def _rule(client, alex, todo, name="Tag it"):
    r = client.post(
        f"{API}/automations",
        json={
            "name": name,
            "list_id": todo["id"],
            "trigger": {"type": "task_created"},
            "actions": [{"type": "add_tag", "tag": "seen"}],
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_an_automation_event_that_raises_is_skipped(client, alex, todo, db, monkeypatch):
    _rule(client, alex, todo)
    automations.start(db)
    bad = _item(client, alex, todo, "Bad")
    good = _item(client, alex, todo, "Good")
    real = automations._handle

    def handle(session, rules, ev, send):
        if ev.item_id == bad["id"]:
            raise KeyError("broken view")
        return real(session, rules, ev, send)

    monkeypatch.setattr(automations, "_handle", handle)
    assert automations.process(db) == 1
    runs = list(db.scalars(select(AutomationRun)))
    assert [r.item_id for r in runs] == [good["id"]]
    assert automations.process(db) == 0  # the cursor moved past both


def test_a_rule_is_not_run_twice_for_one_event_after_a_restart(client, alex, todo, db):
    _rule(client, alex, todo)
    automations.start(db)
    cursor_before = automations.start(db)
    _item(client, alex, todo, "Once")
    assert automations.process(db) == 1
    # A crash after the run was logged but before the cursor moved: put the cursor back.
    row = automations._cursor(db)
    row.event_id = cursor_before
    db.commit()
    assert automations.process(db) == 0
    assert db.scalar(select(func.count()).select_from(AutomationRun)) == 1
