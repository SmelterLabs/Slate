"""Guest browser sessions follow the account's shares, say when they end, warn three days
before, and an admin can extend one or copy a fresh link for another device."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from slate.models import Account, Event, Token, utcnow
from slate.service import inbox, push, sessions
from slate.service.access import EventGate
from slate.service.core import event_payload
from test_collaboration import API, BROWSER, accept, invite


@pytest.fixture(autouse=True)
def fresh_attempts(monkeypatch):
    from slate.api import collaboration

    monkeypatch.setattr(collaboration, "attempts", collaboration.Attempts())


def _private_section(client, alex, name):
    section = client.post(
        f"{API}/sections", json={"name": name, "audience": "work"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{section['id']}", json={"private": True}, headers=alex)
    client.post(f"{API}/lists", json={"section_id": section["id"], "name": "Work"}, headers=alex)
    return section


def test_guest_session_follows_the_accounts_shares(client, alex, todo, db):
    invitation = invite(client, alex, todo)
    later = _private_section(client, alex, "Later project")
    never = _private_section(client, alex, "Never shared")
    assert accept(client, invitation).status_code == 200
    me = client.get(f"{API}/me").json()
    token = db.get(Token, me["token_id"])
    assert token.limit_json == {"level": "edit"}  # no section pin: reach is the shares
    assert me["access"]["sections"] == {todo["section_id"]: "edit"}
    assert not me["access"]["general"] and not me["access"]["manage"]
    assert {s["id"] for s in client.get(f"{API}/sections").json()} == {todo["section_id"]}
    assert client.get(f"{API}/sections/{never['id']}/project").status_code == 404

    gate = EventGate(me["account"]["id"], me["token_id"])
    gate.load(db)
    assert gate.access.section_level(later["id"]) == 0
    # A second section shared later: visible without a new invitation.
    shared = client.put(
        f"{API}/shares",
        json={
            "account_id": me["account"]["id"],
            "scope_kind": "section",
            "scope_id": later["id"],
            "level": "view",
        },
        headers=alex,
    )
    assert shared.status_code in (200, 201), shared.text
    me = client.get(f"{API}/me").json()
    assert me["access"]["sections"] == {todo["section_id"]: "edit", later["id"]: "view"}
    assert {s["id"] for s in client.get(f"{API}/sections").json()} == {
        todo["section_id"],
        later["id"],
    }
    assert client.get(f"{API}/sections/{never['id']}/project").status_code == 404
    # The live stream reloads on the share and then lets the new section's events through.
    granted = db.scalar(
        select(Event).where(Event.type == "share.granted").order_by(Event.id.desc())
    )
    payload = event_payload(granted)
    assert gate.needs_reload(payload) and gate.allows(db, payload)
    gate.load(db)
    assert gate.access.section_level(later["id"]) > 0
    assert gate.access.section_level(never["id"]) == 0
    assert not gate.access.general


def test_me_says_when_a_browser_session_ends(client, alex, todo):
    assert client.get(f"{API}/me", headers=alex).json()["session_expires_at"] is None
    invitation = invite(client, alex, todo)
    assert accept(client, invitation).status_code == 200
    me = client.get(f"{API}/me").json()
    ends = me["session_expires_at"]
    assert ends and ends.startswith((utcnow() + timedelta(days=30)).date().isoformat())
    rows = client.get(f"{API}/invitations?section_id={todo['section_id']}", headers=alex).json()
    assert rows[0]["session_expires_at"] == ends and rows[0]["session_revoked_at"] is None


def test_expiry_notice_once_to_guest_and_inviter(client, alex, todo, db):
    invitation = invite(client, alex, todo)
    assert accept(client, invitation).status_code == 200
    guest_id = client.get(f"{API}/me").json()["account"]["id"]
    guest = db.get(Account, guest_id)
    inviter = db.scalar(select(Account).where(Account.handle == "alex"))
    assert sessions.notify_expiring(db, utcnow() + timedelta(days=20)) == 0
    assert sessions.notify_expiring(db, utcnow() + timedelta(days=27, hours=1)) == 1
    db.expire_all()
    notices = list(db.scalars(select(Event).where(Event.type == "session.expiring")))
    assert {e.subject_id for e in notices} == {guest_id, inviter.id}
    note = next(e for e in notices if e.subject_id == guest_id)
    assert note.after_json["section"] == "Personal" and note.after_json["inviter"] == "alex"
    # The date is Chicago's (UTC's would be a day ahead every evening after 6 pm).
    expires = (utcnow() + timedelta(days=30)).astimezone(sessions.LOCAL)
    assert note.after_json["date"] == expires.date().isoformat()
    # Inbox and push, for both.
    mine = inbox.entries(db, guest)
    assert [e["reason"] for e in mine] == ["access"]
    assert mine[0]["item_title"] == "Access to Personal"
    theirs = [e for e in inbox.entries(db, inviter) if e["reason"] == "access"]
    assert len(theirs) == 1
    told = push.notification_for(db, note, "access")
    assert "ends" in told["title"] and "alex" in told["body"].lower()
    assert told["url"] == f"/#/section/{todo['section_id']}"
    assert "access" in push.DEFAULT_REASONS
    # Idempotent: the next tick (and a restart) sends nothing more.
    assert sessions.notify_expiring(db, utcnow() + timedelta(days=27, hours=2)) == 0
    assert sessions.notify_expiring(db, utcnow() + timedelta(days=29)) == 0
    db.expire_all()
    assert db.scalar(select(Event).where(Event.type == "session.expiring")) is not None
    assert len(list(db.scalars(select(Event).where(Event.type == "session.expiring")))) == 2
    # The scheduler runs it and the Health page knows it.
    from slate import scheduler
    from slate.service import health

    assert sessions.notify_expiring in scheduler.hooks
    assert scheduler.POLLERS[sessions.notify_expiring] in health.POLLERS


def test_admin_extends_a_session_by_thirty_days(client, alex, todo, db):
    invitation = invite(client, alex, todo)
    assert (
        client.post(f"{API}/invitations/{invitation['id']}/extend", headers=alex).status_code == 422
    )
    assert accept(client, invitation).status_code == 200
    me = client.get(f"{API}/me").json()
    with freeze_time(utcnow() + timedelta(days=20)):
        r = client.post(f"{API}/invitations/{invitation['id']}/extend", headers=alex)
        assert r.status_code == 200, r.text
        ends = r.json()["session_expires_at"]
        assert ends.startswith((utcnow() + timedelta(days=30)).date().isoformat())
        assert client.get(f"{API}/me").json()["session_expires_at"] == ends
    with freeze_time(utcnow() + timedelta(days=45)):
        assert client.get(f"{API}/me").status_code == 200  # still signed in on day 45
    db.expire_all()
    extended = db.scalar(select(Event).where(Event.type == "session.extended"))
    assert extended is not None and extended.subject_id == me["token_id"]
    assert extended.actor_id == db.scalar(select(Account.id).where(Account.handle == "alex"))
    # A guest cannot extend; a revoked session cannot be extended.
    assert (
        client.post(f"{API}/invitations/{invitation['id']}/extend", headers=BROWSER).status_code
        == 403
    )
    assert client.delete(f"{API}/invitations/{invitation['id']}", headers=alex).status_code == 204
    assert (
        client.post(f"{API}/invitations/{invitation['id']}/extend", headers=alex).status_code == 410
    )


def test_extension_rearms_the_expiry_notice(client, alex, todo, db):
    invitation = invite(client, alex, todo)
    assert accept(client, invitation).status_code == 200
    assert sessions.notify_expiring(db, utcnow() + timedelta(days=28)) == 1
    with freeze_time(utcnow() + timedelta(days=28)):
        assert (
            client.post(f"{API}/invitations/{invitation['id']}/extend", headers=alex).status_code
            == 200
        )
    db.expire_all()
    assert sessions.notify_expiring(db, utcnow() + timedelta(days=30)) == 0
    assert sessions.notify_expiring(db, utcnow() + timedelta(days=56)) == 1


def test_reissue_mints_a_link_for_another_device(client, alex, todo, db):
    invitation = invite(client, alex, todo)
    r = client.post(f"{API}/invitations/{invitation['id']}/reissue", headers=alex)
    assert r.status_code == 201, r.text
    second = r.json()
    assert second["id"] != invitation["id"]
    assert second["account_id"] == invitation["account_id"]
    assert second["section_id"] == todo["section_id"]
    assert second["secret"].startswith("invite_") and second["secret"] != invitation["secret"]
    assert second["reissued_from"] == invitation["id"]
    # Both links work, each once; the two sessions belong to the same guest.
    assert accept(client, invitation).status_code == 200
    first_me = client.get(f"{API}/me").json()
    client.cookies.clear()
    assert accept(client, second).status_code == 200
    second_me = client.get(f"{API}/me").json()
    assert second_me["account"]["id"] == first_me["account"]["id"]
    assert second_me["token_id"] != first_me["token_id"]
    assert accept(client, second).status_code == 410
    assert len(list(db.scalars(select(Account).where(Account.handle == "guest")))) == 1
    rows = client.get(f"{API}/invitations?section_id={todo['section_id']}", headers=alex).json()
    assert {row["id"] for row in rows} == {invitation["id"], second["id"]}
    # Not for a cancelled invitation, and never for a guest.
    assert (
        client.post(f"{API}/invitations/{invitation['id']}/reissue", headers=BROWSER).status_code
        == 403
    )
    assert client.delete(f"{API}/invitations/{second['id']}", headers=alex).status_code == 204
    assert client.post(f"{API}/invitations/{second['id']}/reissue", headers=alex).status_code == 410
