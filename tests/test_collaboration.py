"""Invitation credentials are single-use, scoped, expiring, and never event payloads."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from slate.models import Event, Source, Token, utcnow
from slate.service.core import Actor, apply_change

API = "/api/v1"
BROWSER = {"X-Slate-Client": "browser", "Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def fresh_attempts(monkeypatch):
    from slate.api import collaboration

    monkeypatch.setattr(collaboration, "attempts", collaboration.Attempts())


def invite(client, alex, todo, **changes):
    client.patch(f"{API}/sections/{todo['section_id']}", json={"private": True}, headers=alex)
    response = client.post(
        f"{API}/invitations",
        headers=alex,
        json={
            "section_id": todo["section_id"],
            "handle": "guest",
            "display_name": "Guest",
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_an_invitation_grants_guest_edit_and_refuses_anything_wider(client, alex, todo):
    """`role` and `level` on an invitation are not ignored: they state the only grant there is."""
    assert invite(client, alex, todo, role="guest", level="edit")["role"] == "guest"
    for wider in ({"role": "member"}, {"level": "view"}):
        r = client.post(
            f"{API}/invitations",
            headers=alex,
            json={
                "section_id": todo["section_id"],
                "handle": "guest2",
                "display_name": "Guest",
                **wider,
            },
        )
        assert r.status_code == 422, r.text


def accept(client, invitation):
    return client.post(
        f"{API}/invitations/accept", json={"secret": invitation["secret"]}, headers=BROWSER
    )


def test_invitation_session_scope_replay_and_logout(client, alex, todo, db):
    invitation = invite(client, alex, todo)
    response = accept(client, invitation)
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "secret" not in response.json() and "token" not in response.json()
    me = client.get(f"{API}/me").json()
    assert me["account"]["role"] == "guest"
    assert me["access"]["sections"] == {todo["section_id"]: "edit"}
    assert not me["access"]["manage"] and not me["access"]["general"]
    accepted = list(db.scalars(select(Event).where(Event.type == "invitation.accepted")))
    assert accepted and all(
        e.actor_id == me["account"]["id"] and e.source == Source.ui for e in accepted
    )
    assert accept(client, invitation).status_code == 410
    assert (
        client.post(
            f"{API}/lists",
            json={"section_id": todo["section_id"], "name": "Ideas"},
            headers=BROWSER,
        ).status_code
        == 201
    )
    assert (
        client.patch(
            f"{API}/sections/{todo['section_id']}", json={"position": 20}, headers=BROWSER
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/sections", json={"name": "Escape", "audience": "work"}, headers=BROWSER
        ).status_code
        == 403
    )
    assert client.get(f"{API}/members").status_code == 403
    assert invitation["secret"] not in str(
        [(e.before_json, e.after_json) for e in db.scalars(select(Event))]
    )
    assert client.post(f"{API}/session/logout", headers=BROWSER).status_code == 204
    assert client.get(f"{API}/me").status_code == 401
    assert client.get(f"{API}/me", headers=alex).status_code == 200


def test_invite_expiry_revocation_and_csrf(client, alex, todo):
    from freezegun import freeze_time

    invitation = invite(client, alex, todo)
    with freeze_time(utcnow() + timedelta(days=8)):
        assert accept(client, invitation).status_code == 410
    assert client.delete(f"{API}/invitations/{invitation['id']}", headers=alex).status_code == 204
    assert accept(client, invitation).status_code == 410
    replacement = invite(client, alex, todo)
    assert accept(client, replacement).status_code == 200
    assert (
        client.post(
            f"{API}/lists", json={"section_id": todo["section_id"], "name": "CSRF"}
        ).status_code
        == 403
    )
    bad = {**BROWSER, "Origin": "https://evil.example"}
    assert client.post(f"{API}/session/logout", headers=bad).status_code == 403
    with freeze_time(utcnow() + timedelta(days=31)):
        assert client.get(f"{API}/me").status_code == 401


def test_revoke_accepted_invitation_revokes_session(client, alex, todo):
    invitation = invite(client, alex, todo)
    assert accept(client, invitation).status_code == 200
    assert client.delete(f"{API}/invitations/{invitation['id']}", headers=alex).status_code == 204
    assert client.get(f"{API}/me").status_code == 401


def test_invite_rejects_existing_privileged_account(client, alex, todo):
    response = client.post(
        f"{API}/invitations",
        headers=alex,
        json={
            "section_id": todo["section_id"],
            "handle": "alex",
            "display_name": "Alex",
        },
    )
    assert response.status_code == 422


def test_project_home_and_whiteboard(client, alex, todo):
    sid = todo["section_id"]
    assert (
        client.patch(
            f"{API}/sections/{sid}", headers=alex, json={"whiteboard_url": "javascript:alert(1)"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"{API}/sections/{sid}",
            headers=alex,
            json={"whiteboard_url": "https://example.com/board"},
        ).status_code
        == 200
    )
    invitation = invite(client, alex, todo)
    assert accept(client, invitation).status_code == 200
    result = client.get(f"{API}/sections/{sid}/project").json()
    assert result["section"]["whiteboard_url"] == "https://example.com/board"
    assert {p["handle"] for p in result["participants"]} == {"alex", "guest"}
    # Role lets the board tell a project's own (guest) agent from a workspace member.
    assert {p["handle"]: p["role"] for p in result["participants"]}["guest"] == "guest"


def test_live_gate_denies_revoked_token(client, alex, todo, db):
    from slate.service.access import EventGate

    invitation = invite(client, alex, todo)
    assert accept(client, invitation).status_code == 200
    me = client.get(f"{API}/me").json()
    gate = EventGate(me["account"]["id"], me["token_id"])
    gate.load(db)
    token = db.get(Token, me["token_id"])
    with apply_change(db, Actor(me["account"]["id"], Source.api)) as cx:
        cx.set(token, "revoked_at", utcnow(), "token.revoked")
    gate.load(db)
    assert not gate.allows(
        db,
        {
            "type": "section.updated",
            "subject_id": todo["section_id"],
            "actor_id": me["account"]["id"],
        },
    )


def test_guest_cookie_session_writes_are_ui_with_the_board_header(client, alex, todo, db):
    assert accept(client, invite(client, alex, todo)).status_code == 200
    board = {**BROWSER, "X-Slate-Source": "ui"}
    made = client.post(
        f"{API}/lists", json={"section_id": todo["section_id"], "name": "Ideas"}, headers=board
    )
    assert made.status_code == 201, made.text
    plain = client.post(
        f"{API}/lists", json={"section_id": todo["section_id"], "name": "Other"}, headers=BROWSER
    )
    assert plain.status_code == 201, plain.text
    spoofed = client.post(
        f"{API}/lists",
        json={"section_id": todo["section_id"], "name": "Spoofed"},
        headers={**BROWSER, "X-Slate-Source": "mcp"},
    )
    assert spoofed.status_code == 201, spoofed.text

    def source(list_id):
        return db.scalar(select(Event.source).where(Event.subject_id == list_id))

    assert source(made.json()["id"]) == Source.ui
    assert source(plain.json()["id"]) == Source.api
    assert source(spoofed.json()["id"]) == Source.api
