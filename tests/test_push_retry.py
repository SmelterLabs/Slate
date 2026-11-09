"""Durable push outcomes: fake transport only, real SQLite sessions and event writes."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from conftest import API
from slate import webpush
from slate.db import SessionLocal
from slate.models import Event, PushDelivery, utcnow
from slate.service import push
from test_assigned_comments import _comment, _item
from test_collaboration_attention import guest
from test_push import Browser


def test_transient_failure_retries_after_restart_without_replaying_success(
    client, alex, todo, db, make_account, monkeypatch
):
    _, author = make_account("author")
    for suffix in ("good", "bad"):
        r = client.post(
            API + "/push/subscriptions",
            headers=alex,
            json=Browser(f"https://push.example.com/{suffix}").body(),
        )
        assert r.status_code == 201
    calls = []
    failing = True

    def send(endpoint, key, auth, note):
        calls.append((endpoint, note["event_id"]))
        return (503, "HTTP 503") if failing and endpoint.endswith("bad") else (201, "")

    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    push.process(db, send)
    task = _item(client, author, todo["id"])
    _comment(client, author, task["id"], "@alex")
    assert push.process(db, send) == 1
    assert len(calls) == 2
    failing = False
    # A genuinely new session, with no in-memory delivery state.
    with SessionLocal() as restarted:
        assert push.process(restarted, send) == 0
        assert len(calls) == 2
        now += timedelta(minutes=2)
        assert push.process(restarted, send) == 1
        assert len(calls) == 3 and calls[-1][0].endswith("bad")
        assert push.process(restarted, send) == 0
        assert len(calls) == 3


@pytest.mark.parametrize("revoke", ["disable", "share", "unsubscribe", "preferences", "ownership"])
def test_retry_rechecks_recipient_eligibility(client, alex, todo, db, monkeypatch, revoke):
    account, headers, share = guest(client, alex, todo)
    sub = client.post(API + "/push/subscriptions", headers=headers, json=Browser().body()).json()
    calls = []

    def send(endpoint, key, auth, note):
        calls.append(note)
        return 503, "HTTP 503"

    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    push.process(db, send)
    task = _item(client, alex, todo["id"])
    _comment(client, alex, task["id"], "Please review", assignee="guest")
    assert push.process(db, send) == 0
    assert len(calls) == 1
    if revoke == "disable":
        r = client.patch(f"{API}/accounts/{account['id']}", headers=alex, json={"disabled": True})
    elif revoke == "share":
        r = client.delete(f"{API}/shares/{share['id']}", headers=alex)
    elif revoke == "unsubscribe":
        r = client.delete(f"{API}/push/subscriptions/{sub['id']}", headers=headers)
    elif revoke == "ownership":
        r = client.post(API + "/push/subscriptions", headers=alex, json=Browser().body())
    else:
        r = client.patch(API + "/me/prefs", headers=headers, json={"push_reasons": []})
    assert r.status_code in (200, 201, 204), r.text
    now += timedelta(minutes=2)
    with SessionLocal() as restarted:
        assert push.process(restarted, send) == 0
        assert len(calls) == 1


def test_retries_are_bounded_and_old_events_never_blast(
    client, alex, todo, db, make_account, monkeypatch
):
    _, author = make_account("author")
    client.post(API + "/push/subscriptions", headers=alex, json=Browser().body())
    calls = []

    def send(endpoint, key, auth, note):
        calls.append(note)
        return 503, "HTTP 503"

    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    task = _item(client, author, todo["id"])
    _comment(client, author, task["id"], "@alex historical")
    assert push.process(db, send) == 0  # first activation seeds; no history
    _comment(client, author, task["id"], "@alex new")
    push.process(db, send)
    for _ in range(8):
        now += timedelta(hours=1)
        with SessionLocal() as restarted:
            push.process(restarted, send)
    assert len(calls) == 5  # first try plus four retries
    _comment(client, author, task["id"], "@alex while down")
    now += timedelta(days=2)
    push.process(db, send)
    assert len(calls) == 5


@pytest.mark.parametrize(
    "header,seconds", [("900", 900), ("bad", 30), ("99999999", 21600), ("-1", 30)]
)
def test_retry_after_from_transport_is_bounded(
    client, alex, todo, db, make_account, monkeypatch, header, seconds
):
    _, author = make_account("author")
    browser = Browser()
    client.post(API + "/push/subscriptions", headers=alex, json=browser.body())
    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    monkeypatch.setattr(
        webpush,
        "transport",
        httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": header})),
    )
    push.process(db, webpush.send)
    task = _item(client, author, todo["id"])
    _comment(client, author, task["id"], "@alex")
    assert push.process(db, webpush.send) == 0
    row = db.scalar(select(PushDelivery))
    assert row.attempts == 1 and row.state == "pending"
    assert row.next_attempt_at == now + timedelta(seconds=seconds)
    # Receipts are bookkeeping (the table is the delivery log), never events; and no event of
    # any kind carries the endpoint or the browser's keys.
    assert db.scalars(select(Event).where(Event.type == "push.delivery")).all() == []
    events = db.scalars(select(Event)).all()
    assert browser.endpoint not in str([e.after_json for e in events])
    assert browser.p256dh not in str([e.after_json for e in events])


def test_delivery_attempts_are_receipts_not_events(
    client, alex, todo, db, make_account, monkeypatch
):
    """Two source events x two devices queue four receipts, send them and expire them a day
    later without one event beyond the two that caused them (a `push.delivery` event per
    attempt would reach every board, webhook, automation and check-in scan)."""
    _, author = make_account("author")
    for suffix in ("a", "b"):
        client.post(
            API + "/push/subscriptions",
            headers=alex,
            json=Browser(f"https://push.example.com/{suffix}").body(),
        )
    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    push.process(db, lambda *args: (201, ""))
    task = _item(client, author, todo["id"])
    before = db.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))
    _comment(client, author, task["id"], "@alex one")
    _comment(client, author, task["id"], "@alex two")
    assert push.process(db, lambda *args: (201, "")) == 4
    rows = db.scalars(select(PushDelivery)).all()
    assert len(rows) == 4 and {r.state for r in rows} == {"sent"}
    new = db.scalars(select(Event).where(Event.id > before).order_by(Event.id)).all()
    assert [e.type for e in new] == ["item.commented", "item.commented"]
    now += timedelta(days=1, minutes=1)
    with SessionLocal() as restarted:
        assert push.process(restarted, lambda *args: (201, "")) == 0
        assert restarted.scalars(select(PushDelivery)).all() == []
        newest = restarted.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))
        assert newest == new[-1].id


def test_failed_renudge_survives_restart_but_read_cancels_retry(
    client, alex, todo, db, monkeypatch
):
    _, headers, _ = guest(client, alex, todo)
    client.post(API + "/push/subscriptions", headers=headers, json=Browser().body())
    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    push.process(db, lambda *args: (201, ""))
    key = f"list:{todo['id']}"
    client.post(f"{API}/channels/{key}/messages", headers=alex, json={"body_md": "Update"})
    assert push.process(db, lambda *args: (201, "")) == 1
    now += timedelta(minutes=11)
    assert push.renudge(db, lambda *args: (503, "HTTP 503"), {}, now=now) == 0
    rows = db.scalars(select(PushDelivery).where(PushDelivery.kind == "renudge")).all()
    assert len(rows) == 1 and rows[0].attempts == 1
    now += timedelta(minutes=1)
    received = []
    with SessionLocal() as restarted:
        assert push.process(restarted, lambda *args: (received.append(args[3]) or 201, "")) == 1
    assert len(received) == 1 and received[0]["renudge"]
    # A second new message can receive one nudge; reading it cancels its pending retry.
    client.post(f"{API}/channels/{key}/messages", headers=alex, json={"body_md": "Another"})
    push.process(db, lambda *args: (201, ""))
    now += timedelta(minutes=11)
    push.renudge(db, lambda *args: (503, "HTTP 503"), {}, now=now)
    assert client.post(f"{API}/channels/{key}/read", headers=headers, json={}).status_code == 200
    now += timedelta(minutes=1)
    with SessionLocal() as restarted:
        assert push.process(restarted, lambda *args: (received.append(args[3]) or 201, "")) == 0
    assert len(received) == 1


def test_retry_rerenders_references_after_access_loss(client, alex, todo, db, monkeypatch):
    account, headers, _ = guest(client, alex, todo)
    browser = Browser()
    client.post(API + "/push/subscriptions", headers=headers, json=browser.body())
    private = client.post(
        API + "/lists", headers=alex, json={"section_id": todo["section_id"], "name": "Private"}
    ).json()
    secret = _item(client, alex, private["id"], title="Confidential budget")
    share = client.put(
        API + "/shares",
        headers=alex,
        json={
            "account_id": account["id"],
            "scope_kind": "list",
            "scope_id": private["id"],
            "level": "view",
        },
    ).json()
    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    received = []

    def send(*args):
        received.append(args[3])
        return (503, "HTTP 503") if len(received) == 1 else (201, "")

    push.process(db, send)
    client.post(
        f"{API}/channels/list:{todo['id']}/messages",
        headers=alex,
        json={"body_md": f"See #{secret['id']}"},
    )
    push.process(db, send)
    assert "Confidential budget" in received[0]["body"]
    assert client.delete(f"{API}/shares/{share['id']}", headers=alex).status_code == 204
    now += timedelta(minutes=2)
    with SessionLocal() as restarted:
        assert push.process(restarted, send) == 1
    assert "Confidential budget" not in received[1]["body"]


def test_commit_before_transport_and_recover_pending_after_crash(
    client, alex, todo, db, make_account
):
    _, author = make_account("author")
    client.post(API + "/push/subscriptions", headers=alex, json=Browser().body())
    push.process(db, lambda *args: (201, ""))
    task = _item(client, author, todo["id"])
    _comment(client, author, task["id"], "@alex")

    class ProcessDeath(BaseException):
        pass

    def crash(*args):
        with SessionLocal() as observer:
            row = observer.scalar(select(PushDelivery))
            assert row is not None and row.state == "pending" and row.attempts == 0
        raise ProcessDeath()

    with pytest.raises(ProcessDeath):
        push.process(db, crash)
    with SessionLocal() as restarted:
        assert push.process(restarted, lambda *args: (201, "")) == 1
        assert restarted.scalar(select(PushDelivery)).state == "sent"


def test_retry_after_http_date_and_nonfinite_values():
    from email.utils import format_datetime

    now = utcnow().replace(microsecond=0)
    assert push._retry_delay(1, format_datetime(now + timedelta(minutes=5)), now) == timedelta(
        minutes=5
    )
    for value in ("nan", "inf", "-inf", "yesterday", "-5"):
        assert push._retry_delay(1, value, now) == timedelta(seconds=30)


def test_renudge_uses_chat_preference_not_original_mention_reason(client, alex, todo, db):
    _, headers, _ = guest(client, alex, todo)
    client.post(API + "/push/subscriptions", headers=headers, json=Browser().body())
    client.patch(API + "/me/prefs", headers=headers, json={"push_reasons": ["chat"]})
    client.post(
        f"{API}/channels/list:{todo['id']}/messages",
        headers=alex,
        json={"body_md": "@guest a question"},
    )
    received = []
    assert (
        push.renudge(
            db,
            lambda *args: (received.append(args[3]) or 201, ""),
            {},
            now=utcnow() + timedelta(minutes=11),
        )
        == 1
    )
    assert received[0]["renudge"]


def test_sqlite_retry_receipts_survive_a_new_interpreter(
    client, alex, todo, db, make_account, tmp_path
):
    import json
    import os
    import sqlite3
    import subprocess
    import sys

    _, author = make_account("author")
    client.post(API + "/push/subscriptions", headers=alex, json=Browser().body())
    push.process(db, lambda *args: (201, ""))
    task = _item(client, author, todo["id"])
    _comment(client, author, task["id"], "@alex")
    assert push.process(db, lambda *args: (503, "HTTP 503")) == 0
    path = tmp_path / "retry.sqlite3"
    # Copy the synthetic fixture as SQLite itself does, then reopen it in fresh processes.
    with sqlite3.connect(path) as target:
        connection = db.get_bind().raw_connection()
        try:
            connection.driver_connection.backup(target)
        finally:
            connection.close()
    script = """
import json
from datetime import timedelta
from sqlalchemy import select
from slate.db import SessionLocal
from slate.models import PushDelivery, utcnow
from slate.service import push
now = utcnow() + timedelta(minutes=2)
push.utcnow = lambda: now
with SessionLocal() as session:
    sent = push.process(session, lambda *args: (201, ""))
    print(json.dumps({"sent": sent, "states": list(session.scalars(select(PushDelivery.state)))}))
"""
    env = {**os.environ, "SLATE_DATABASE_PATH": str(path)}
    outcomes = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        outcomes.append(json.loads(result.stdout))
    assert outcomes == [{"sent": 1, "states": ["sent"]}, {"sent": 0, "states": ["sent"]}]
