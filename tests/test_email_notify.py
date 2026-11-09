"""Email notifications: chosen reasons, the unseen delay, one email per burst, and opt-in."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from slate import mailer
from slate.models import EmailDelivery, utcnow
from slate.service import email

API = "/api/v1"
LATER = timedelta(minutes=6)


class Outbox:
    def __init__(self):
        self.sent: list[tuple[str, str, str, str]] = []
        self.error: str | None = None

    def __call__(self, to, subject, text, html):
        if self.error:
            return self.error
        self.sent.append((to, subject, text, html))
        return None


def _item(client, headers, todo, title="Fix gutter"):
    return client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": title}, headers=headers
    ).json()


def _opt_in(client, alex, **extra):
    r = client.patch(
        f"{API}/me/prefs", json={"email_to": "alex@example.com", **extra}, headers=alex
    )
    assert r.status_code == 200, r.text


def test_unseen_mention_is_emailed_after_the_delay(client, alex, todo, db, make_account):
    _opt_in(client, alex)
    out = Outbox()
    email.process(db, out)  # cursor starts now
    _, agent = make_account("agent")
    it = _item(client, agent, todo)
    client.post(f"{API}/items/{it['id']}/comments", json={"body_md": "Look @alex"}, headers=agent)
    client.patch(f"{API}/items/{it['id']}", json={"title": "Fix the gutter"}, headers=agent)

    assert email.process(db, out) == 0  # queued, not yet due
    assert out.sent == []
    assert email.process(db, out, now=utcnow() + LATER) == 1
    (to, subject, text, html) = out.sent[0]
    assert to == "alex@example.com"
    assert subject == "Agent mentioned you"  # only the mention: updates are not chosen
    assert "Fix the gutter: Look @alex" in text and "/#/inbox?task=" in text
    assert "Agent mentioned you" in html
    assert email.process(db, out, now=utcnow() + LATER) == 0  # sent once


def test_seen_on_the_board_is_not_emailed(client, alex, todo, db, make_account):
    _opt_in(client, alex)
    out = Outbox()
    email.process(db, out)
    _, agent = make_account("agent")
    a = _item(client, agent, todo, "Opened card")
    b = _item(client, agent, todo, "Read in inbox")
    client.post(f"{API}/items/{a['id']}/comments", json={"body_md": "@alex a"}, headers=agent)
    client.post(f"{API}/items/{b['id']}/comments", json={"body_md": "@alex b"}, headers=agent)
    email.process(db, out)
    assert client.post(f"{API}/items/{a['id']}/read", headers=alex).status_code == 200
    r = client.post(f"{API}/inbox/read", json={"item_id": b["id"]}, headers=alex)
    assert r.status_code == 200, r.text
    assert email.process(db, out, now=utcnow() + LATER) == 0
    assert out.sent == []
    states = {d.state for d in db.scalars(select(EmailDelivery))}
    assert states == {"dropped"}


def test_a_burst_is_one_email(client, alex, todo, db, make_account):
    _opt_in(client, alex, email_reasons=["mention", "assigned"])
    out = Outbox()
    email.process(db, out)
    _, agent = make_account("agent")
    for n in range(3):
        it = _item(client, agent, todo, f"Task {n}")
        client.patch(f"{API}/items/{it['id']}", json={"assignees": ["alex"]}, headers=agent)
    email.process(db, out)
    assert email.process(db, out, now=utcnow() + LATER) == 1
    assert out.sent[0][1] == "3 things in Slate need you"


def test_no_address_or_no_reasons_means_no_email(client, alex, todo, db, make_account):
    out = Outbox()
    email.process(db, out)
    _, agent = make_account("agent")
    it = _item(client, agent, todo)
    client.post(f"{API}/items/{it['id']}/comments", json={"body_md": "@alex"}, headers=agent)
    email.process(db, out)
    _opt_in(client, alex, email_reasons=[])
    client.post(f"{API}/items/{it['id']}/comments", json={"body_md": "@alex 2"}, headers=agent)
    email.process(db, out)
    assert email.process(db, out, now=utcnow() + LATER) == 0
    assert db.scalar(select(EmailDelivery)) is None


def test_a_failed_send_retries_then_succeeds(client, alex, todo, db, make_account):
    _opt_in(client, alex)
    out = Outbox()
    email.process(db, out)
    _, agent = make_account("agent")
    it = _item(client, agent, todo)
    client.post(f"{API}/items/{it['id']}/comments", json={"body_md": "@alex"}, headers=agent)
    email.process(db, out)
    out.error = "SMTPServerDisconnected: gone"
    assert email.process(db, out, now=utcnow() + LATER) == 0
    row = db.scalar(select(EmailDelivery))
    assert row.state == "pending" and row.attempts == 1
    out.error = None
    assert email.process(db, out, now=utcnow() + LATER * 3) == 1


def test_prefs_are_validated(client, alex):
    bad = client.patch(f"{API}/me/prefs", json={"email_to": "not an address"}, headers=alex)
    assert bad.status_code == 422
    bad = client.patch(f"{API}/me/prefs", json={"email_reasons": ["chat"]}, headers=alex)
    assert bad.status_code == 422  # chat is a toast, never an email
    ok = client.patch(f"{API}/me/prefs", json={"email_reasons": ["mention"]}, headers=alex)
    assert ok.status_code == 200


def test_me_says_whether_email_is_set_up_and_test_sends(client, alex, monkeypatch):
    assert client.get(f"{API}/me", headers=alex).json()["email"] is False
    assert client.post(f"{API}/email/test", headers=alex).status_code == 503
    monkeypatch.setattr(mailer, "enabled", lambda: True)
    out = Outbox()
    monkeypatch.setattr(mailer, "send", out)
    assert client.get(f"{API}/me", headers=alex).json()["email"] is True
    r = client.post(f"{API}/email/test", headers=alex).json()
    assert r == {"ok": False, "error": "set an email address first"}
    _opt_in(client, alex)
    assert client.post(f"{API}/email/test", headers=alex).json() == {"ok": True, "error": None}
    assert out.sent[0][0] == "alex@example.com"
