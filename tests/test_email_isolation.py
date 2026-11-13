"""One bad email never stops the others: a line break in a title, or a sender that raises."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from slate import mailer
from slate.models import EmailDelivery, utcnow
from slate.service import email

API = "/api/v1"
LATER = timedelta(minutes=6)


class FakeSMTP:
    """smtplib.SMTP without a network: records each message handed to it."""

    wire: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        pass

    def has_extn(self, _name):
        return False

    def send_message(self, msg):
        FakeSMTP.wire.append(msg)


def _opt_in(client, headers, address):
    r = client.patch(f"{API}/me/prefs", json={"email_to": address}, headers=headers)
    assert r.status_code == 200, r.text


def _mention(client, agent, todo, handle, title):
    it = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": title}, headers=agent
    ).json()
    r = client.post(
        f"{API}/items/{it['id']}/comments", json={"body_md": f"look @{handle}"}, headers=agent
    )
    assert r.status_code == 201, r.text
    return it


def _smtp(monkeypatch):
    FakeSMTP.wire = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(mailer.settings, "smtp_from", "slate@example.com")
    monkeypatch.setattr(mailer.settings, "smtp_port", 587)


def test_a_line_break_in_a_title_is_flattened_in_the_subject(
    client, alex, todo, db, make_account, monkeypatch
):
    _smtp(monkeypatch)
    _opt_in(client, alex, "alex@example.com")
    email.process(db, mailer.send)  # the cursor starts now
    agent_account, agent = make_account("my-agent")
    r = client.patch(
        f"{API}/accounts/{agent_account['id']}",
        json={"display_name": "My\nAgent"},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    _mention(client, agent, todo, "alex", "Pay invoice\nBcc: x@example.com")
    email.process(db, mailer.send)
    assert email.process(db, mailer.send, now=utcnow() + LATER) == 1
    [msg] = FakeSMTP.wire
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert msg["Bcc"] is None
    assert db.scalar(select(EmailDelivery)).state == "sent"


def test_a_message_that_cannot_be_built_is_an_error_not_an_exception(monkeypatch):
    _smtp(monkeypatch)
    error = mailer.send("alex@example.com", "Two\nlines", "text")
    assert error is None or isinstance(error, str)
    error = mailer.send("alex@example.com\nBcc: x@example.com", "Subject", "text")
    assert isinstance(error, str) and "ValueError" in error
    assert all(m["Bcc"] is None for m in FakeSMTP.wire)


def test_a_sender_that_raises_for_one_account_does_not_stop_the_next(
    client, alex, todo, db, make_account
):
    _opt_in(client, alex, "alex@example.com")
    _, other = make_account("other", kind="person")
    _opt_in(client, other, "other@example.com")
    sent = []

    def send(to, subject, text, html):
        if to == "alex@example.com":
            raise RuntimeError("boom")
        sent.append(to)

    email.process(db, send)
    _, agent = make_account("my-agent")
    _mention(client, agent, todo, "alex", "First")
    _mention(client, agent, todo, "other", "Second")
    email.process(db, send)
    assert email.process(db, send, now=utcnow() + LATER) == 1
    assert sent == ["other@example.com"]
    rows = {r.account_id: r for r in db.scalars(select(EmailDelivery))}
    alex_id = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    assert rows[alex_id].state == "pending" and rows[alex_id].attempts == 1
