"""Web Push: subscriptions, what is sent (decrypted as a browser would), and expiry."""

from __future__ import annotations

import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import select

from slate import webpush
from slate.models import Event, PushSubscription
from slate.service import push

API = "/api/v1"


class Browser:
    """One browser's push subscription: its keys, and what its push service received."""

    def __init__(self, endpoint="https://push.example.com/send/abc"):
        self.endpoint = endpoint
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = webpush.b64url(b"0123456789abcdef")
        self.p256dh = webpush.b64url(webpush._raw_public(self.key.public_key()))
        self.received: list[dict] = []
        self.headers: list[httpx.Headers] = []
        self.status = 201

    def body(self, label="Phone"):
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
            "label": label,
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == self.endpoint
        self.headers.append(request.headers)
        self.received.append(json.loads(webpush.decrypt(request.content, self.key, self.auth)))
        return httpx.Response(self.status)


@pytest.fixture
def browser(monkeypatch):
    b = Browser()
    monkeypatch.setattr(webpush, "transport", httpx.MockTransport(b.handle))
    return b


def item(client, headers, todo, **extra):
    r = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": "Fix gutter", **extra},
        headers=headers,
    )
    return r.json()


def test_encrypt_round_trip():
    ua = ec.generate_private_key(ec.SECP256R1())
    p256dh = webpush.b64url(webpush._raw_public(ua.public_key()))
    auth = webpush.b64url(b"\x07" * 16)
    body = webpush.encrypt(b'{"hi":1}', p256dh, auth)
    assert body[16:20] == (4096).to_bytes(4, "big") and body[20] == 65
    assert webpush.decrypt(body, ua, auth) == b'{"hi":1}'


def test_vapid_header_is_a_valid_es256_token():
    header = webpush.vapid_header("https://fcm.googleapis.com/fcm/send/x")
    t = header.split("t=")[1].split(",")[0]
    k = header.split("k=")[1]
    assert k == webpush.public_key()
    head, claims, sig = t.split(".")
    assert json.loads(webpush.unb64url(claims))["aud"] == "https://fcm.googleapis.com"
    raw = webpush.unb64url(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64url(k))
    public.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


def test_subscribe_list_and_turn_off(client, alex, db, browser):
    key = client.get(f"{API}/push/key", headers=alex).json()
    assert key["enabled"] and key["public_key"] == webpush.public_key()

    r = client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["endpoint_host"] == "push.example.com" and sub["label"] == "Phone"
    assert "p256dh" not in sub and "auth" not in sub
    # The same browser again updates in place.
    again = client.post(f"{API}/push/subscriptions", json=browser.body("Pixel"), headers=alex)
    assert again.json()["id"] == sub["id"]
    assert [s["label"] for s in client.get(f"{API}/push/subscriptions", headers=alex).json()] == [
        "Pixel"
    ]
    # The keys never reach the event log.
    events = db.scalars(select(Event).where(Event.type == "push.subscribed")).all()
    assert len(events) == 2
    assert all(browser.p256dh not in json.dumps(e.after_json) for e in events)

    r = client.post(f"{API}/push/unsubscribe", json={"endpoint": browser.endpoint}, headers=alex)
    assert r.status_code == 204
    assert client.get(f"{API}/push/subscriptions", headers=alex).json() == []


def test_bad_subscriptions_are_refused(client, alex, browser, make_account):
    bad = browser.body()
    bad["endpoint"] = "http://push.example.com/x"
    assert client.post(f"{API}/push/subscriptions", json=bad, headers=alex).status_code == 422
    bad = browser.body()
    bad["keys"]["p256dh"] = "short"
    assert client.post(f"{API}/push/subscriptions", json=bad, headers=alex).status_code == 422
    sub = client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex).json()
    _, agent = make_account("agent")
    r = client.delete(f"{API}/push/subscriptions/{sub['id']}", headers=agent)
    assert r.status_code == 403


def test_mentions_and_assignments_are_pushed(client, alex, todo, db, browser, make_account):
    client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    push.process(db, webpush.send)  # the cursor starts now: no history replayed
    assert browser.received == []

    _, agent = make_account("agent")
    it = item(client, agent, todo)
    client.post(f"{API}/items/{it['id']}/comments", json={"body_md": "Look @alex"}, headers=agent)
    client.patch(f"{API}/items/{it['id']}", json={"assignees": ["alex"]}, headers=agent)
    client.patch(f"{API}/items/{it['id']}", json={"title": "Fix the gutter"}, headers=agent)

    # Being assigned auto-watches the task, so the title change is pushed too.
    assert push.process(db, webpush.send) == 3
    assert [n["reason"] for n in browser.received] == ["mention", "assigned", "update"]
    mention = browser.received[0]
    assert mention["title"] == "Agent mentioned you"
    assert mention["body"] == "Fix the gutter: Look @alex"  # the title as it is now
    assert mention["url"] == f"/#/inbox?task={it['id']}" and mention["tag"] == it["id"]
    assert browser.headers[0]["content-encoding"] == "aes128gcm"
    assert browser.headers[0]["authorization"].startswith("vapid t=")

    assert push.process(db, webpush.send) == 0  # idempotent
    db.expire_all()
    assert db.scalar(select(PushSubscription)).last_ok_at is not None


def test_push_reasons_choose_what_is_pushed(client, alex, todo, db, browser, make_account):
    client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    r = client.patch(f"{API}/me/prefs", json={"push_reasons": ["assigned"]}, headers=alex)
    assert r.status_code == 200, r.text
    push.process(db, webpush.send)
    _, agent = make_account("agent")
    it = item(client, agent, todo)
    client.post(f"{API}/items/{it['id']}/comments", json={"body_md": "@alex"}, headers=agent)
    client.patch(f"{API}/items/{it['id']}", json={"assignees": ["alex"]}, headers=agent)
    push.process(db, webpush.send)
    assert [n["reason"] for n in browser.received] == ["assigned"]
    bad = client.patch(f"{API}/me/prefs", json={"push_reasons": ["gossip"]}, headers=alex)
    assert bad.status_code == 422


def test_a_forgotten_subscription_is_removed(client, alex, todo, db, browser, make_account):
    sub = client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex).json()
    push.process(db, webpush.send)
    browser.status = 410
    _, agent = make_account("agent")
    it = item(client, agent, todo)
    client.patch(f"{API}/items/{it['id']}", json={"assignees": ["alex"]}, headers=agent)
    assert push.process(db, webpush.send) == 0
    db.expire_all()
    assert db.get(PushSubscription, sub["id"]) is None
    (ev,) = db.scalars(select(Event).where(Event.type == "push.expired")).all()
    assert ev.source.value == "system" and ev.before_json["why"] == "HTTP 410"


def test_failures_are_counted_then_given_up(client, alex, todo, db, browser, make_account):
    sub = client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex).json()
    browser.status = 500
    out = client.post(f"{API}/push/test", headers=alex).json()
    assert out[0]["ok"] is False and out[0]["error"] == "HTTP 500"
    db.expire_all()
    assert db.get(PushSubscription, sub["id"]).failures == 1
    browser.status = 201
    out = client.post(f"{API}/push/test", headers=alex).json()
    assert out[0]["ok"] is True
    assert browser.received[-1]["title"] == "Slate notifications are on"
    db.expire_all()
    assert db.get(PushSubscription, sub["id"]).failures == 0


def test_reminders_reach_the_lock_screen(client, alex, todo, db, browser):
    from datetime import UTC, datetime

    from slate.service import reminders

    client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    push.process(db, webpush.send)
    it = item(client, alex, todo)
    client.post(
        f"{API}/items/{it['id']}/reminders",
        json={"at": "2030-01-09T15:00:00Z", "note": "Call the roofer"},
        headers=alex,
    )
    reminders.fire_reminders(db, now=datetime(2030, 1, 9, 16, tzinfo=UTC))
    push.process(db, webpush.send)
    assert [(n["title"], n["body"]) for n in browser.received] == [
        ("⏰ Fix gutter", "Call the roofer")
    ]


def test_chat_messages_are_pushed_to_whoever_can_read_the_channel(
    client, alex, todo, db, browser, make_account
):
    """A plain message in a channel is a `chat` push (on by default) to everyone who can read
    the channel but the author; a guest the list is not shared with hears nothing; a reply in a
    thread the account wrote in stays a `reply`. None of it lands in the Inbox."""
    client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    guest = client.post(
        f"{API}/accounts",
        json={"handle": "guest", "display_name": "Guest", "kind": "person", "role": "guest"},
        headers=alex,
    ).json()
    guest_browser = Browser("https://push.example.com/send/guest")
    tok = client.post(
        f"{API}/accounts/{guest['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    )
    guest_h = {"Authorization": f"Bearer {tok.json()['token']}"}
    both = {browser.endpoint: browser, guest_browser.endpoint: guest_browser}
    webpush.transport = httpx.MockTransport(lambda req: both[str(req.url)].handle(req))
    assert (
        client.post(
            f"{API}/push/subscriptions", json=guest_browser.body(), headers=guest_h
        ).status_code
        == 201
    )
    push.process(db, webpush.send)

    _, agent = make_account("agent")
    key = f"list:{todo['id']}"
    r = client.post(
        f"{API}/channels/{key}/messages", json={"body_md": "Gutter is done"}, headers=agent
    )
    assert r.status_code == 201, r.text
    root = r.json()
    # Alex's own message is not news to Alex; the agent replying under it is a reply.
    client.post(
        f"{API}/channels/{key}/messages",
        json={"body_md": "Nice", "parent_id": root["id"]},
        headers=alex,
    )
    client.post(
        f"{API}/channels/{key}/messages",
        json={"body_md": "Thanks", "parent_id": root["id"]},
        headers=agent,
    )

    assert push.process(db, webpush.send) == 2
    assert [n["reason"] for n in browser.received] == ["chat", "reply"]
    first = browser.received[0]
    assert first["title"] == "Agent in #Todo"
    assert first["body"] == "Gutter is done"
    assert first["url"] == f"/#/chat/{key}?thread={root['id']}"
    assert first["tag"] == f"chat:{key}"  # a busy channel replaces its toast, not stacks it
    assert guest_browser.received == []  # Todo is not shared with the guest

    entries = client.get(f"{API}/inbox", headers=alex).json()
    assert all(e["reason"] != "chat" for e in entries)

    # Turning the reason off stops it.
    client.patch(f"{API}/me/prefs", json={"push_reasons": ["mention"]}, headers=alex)
    client.post(f"{API}/channels/{key}/messages", json={"body_md": "More"}, headers=agent)
    assert push.process(db, webpush.send) == 0


def test_chat_still_unread_is_nudged_once_more(client, alex, todo, db, browser, make_account):
    """Ten minutes after the newest unread message, one more push per channel; reading the
    channel or a newer message resets it; the first run after a start seeds without sending."""
    from datetime import timedelta

    from slate.models import utcnow

    client.post(f"{API}/push/subscriptions", json=browser.body(), headers=alex)
    push.process(db, webpush.send)
    _, agent = make_account("agent")
    key = f"list:{todo['id']}"
    client.post(f"{API}/channels/{key}/messages", json={"body_md": "Gutter is done"}, headers=agent)
    client.post(
        f"{API}/channels/{key}/messages", json={"body_md": "Photos attached"}, headers=agent
    )
    push.process(db, webpush.send)
    assert [n["reason"] for n in browser.received] == ["chat", "chat"]
    browser.received.clear()

    nudged: push.Nudged = {}
    now = utcnow()
    assert push.renudge(db, webpush.send, nudged, now=now + timedelta(minutes=5)) == 0  # too soon
    assert push.renudge(db, webpush.send, nudged, now=now + timedelta(minutes=11)) == 1
    (note,) = browser.received
    assert note["title"] == "Still unread: #Todo"
    assert note["body"] == "2 messages from Agent, waiting since you last looked"
    assert note["url"] == f"/#/chat/{key}" and note["tag"] == f"chat:{key}" and note["renudge"]
    # Not again for the same newest message.
    assert push.renudge(db, webpush.send, nudged, now=now + timedelta(hours=2)) == 0
    # A newer message starts the clock over, once.
    client.post(f"{API}/channels/{key}/messages", json={"body_md": "Invoice too"}, headers=agent)
    browser.received.clear()
    later = utcnow()
    assert push.renudge(db, webpush.send, nudged, now=later + timedelta(minutes=11)) == 1
    assert browser.received[0]["body"].startswith("3 messages from Agent")
    # Reading the channel ends it; Alex's own message never counts.
    r = client.post(f"{API}/channels/{key}/read", json={}, headers=alex)
    assert r.status_code in (200, 204), r.text
    client.post(f"{API}/channels/{key}/messages", json={"body_md": "Thanks"}, headers=alex)
    assert push.renudge(db, webpush.send, nudged, now=later + timedelta(hours=1)) == 0

    # Off for the account, or seeded at start: nothing is sent.
    client.post(f"{API}/channels/{key}/messages", json={"body_md": "One more"}, headers=agent)
    fresh: push.Nudged = {}
    assert push.renudge(db, webpush.send, fresh, now=utcnow() + timedelta(hours=1), seed=True) == 0
    assert push.renudge(db, webpush.send, fresh, now=utcnow() + timedelta(hours=1)) == 0
    client.patch(f"{API}/me/prefs", json={"chat_renudge_minutes": 0}, headers=alex)
    assert push.renudge(db, webpush.send, {}, now=utcnow() + timedelta(hours=1)) == 0
    bad = client.patch(f"{API}/me/prefs", json={"chat_renudge_minutes": 9999}, headers=alex)
    assert bad.status_code == 422
