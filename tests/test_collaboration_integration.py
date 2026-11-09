"""Cross-feature contracts: exact chat reads, durable push and document reply attention."""

from datetime import timedelta

from conftest import API
from slate.db import SessionLocal
from slate.models import Account, utcnow
from slate.service import push
from test_collaboration_attention import guest
from test_doc_threads import _comment, _doc
from test_push import Browser


def test_renudge_counts_only_unexposed_messages(client, alex, todo, db):
    who, headers, _ = guest(client, alex, todo)
    key = f"list:{todo['id']}"
    root = client.post(
        f"{API}/channels/{key}/messages", headers=alex, json={"body_md": "Root"}
    ).json()
    reply = client.post(
        f"{API}/channels/{key}/messages",
        headers=alex,
        json={"body_md": "Unopened reply", "parent_id": root["id"]},
    ).json()
    read = client.post(
        f"{API}/channels/{key}/read", headers=headers, json={"message_ids": [root["id"]]}
    )
    assert read.status_code == 200
    unread = push.unread_by_channel(db, db.get(Account, who["id"]))
    assert [(u.count, u.newest_id) for u in unread] == [(1, reply["id"])]


def test_exact_exposure_cancels_pending_reminder_after_restart(client, alex, todo, db, monkeypatch):
    _, headers, _ = guest(client, alex, todo)
    client.post(API + "/push/subscriptions", headers=headers, json=Browser().body())
    now = utcnow()
    monkeypatch.setattr(push, "utcnow", lambda: now)
    push.process(db, lambda *args: (201, ""))
    key = f"list:{todo['id']}"
    msg = client.post(
        f"{API}/channels/{key}/messages", headers=alex, json={"body_md": "Read me"}
    ).json()
    assert push.process(db, lambda *args: (201, "")) == 1
    now += timedelta(minutes=11)
    assert push.renudge(db, lambda *args: (503, "HTTP 503"), {}, now=now) == 0
    assert (
        client.post(
            f"{API}/channels/{key}/read", headers=headers, json={"message_ids": [msg["id"]]}
        ).status_code
        == 200
    )
    now += timedelta(minutes=1)
    with SessionLocal() as restarted:
        assert push.process(restarted, lambda *args: (201, "")) == 0


def test_document_reply_uses_reply_preference_without_fresh_mention(client, alex, make_account, db):
    _, member = make_account("member", kind="person")
    doc = _doc(client, alex, title="Shared plan")
    ask = _comment(client, alex, doc["id"], body_md="Please review", assignee="member")
    assert (
        client.patch(API + "/me/prefs", headers=alex, json={"push_reasons": ["reply"]}).status_code
        == 200
    )
    assert (
        client.post(API + "/push/subscriptions", headers=alex, json=Browser().body()).status_code
        == 201
    )
    received = []

    def send(endpoint, key, auth, note):
        received.append(note)
        return 201, ""

    push.process(db, send)
    reply = _comment(client, member, doc["id"], body_md="I need more time", parent_id=ask["id"])
    entries = client.get(API + "/inbox", headers=alex).json()
    entry = next(e for e in entries if e["event"]["after"].get("comment_id") == reply["id"])
    assert entry["reason"] == "reply"
    assert push.process(db, send) == 1
    assert received[0]["reason"] == "reply"
    assert "replied" in received[0]["title"]
    threads = client.get(f"{API}/docs/{doc['id']}/comments", headers=alex).json()
    assert next(t for t in threads if t["id"] == ask["id"])["resolved_at"] is None
