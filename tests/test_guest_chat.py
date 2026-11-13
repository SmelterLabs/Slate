"""What a project's guests reach in chat: the push re-nudge counts only what they can read,
an @mention never confirms a handle they cannot see, and an owner or admin can take a message
down."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import API
from slate.models import utcnow
from slate.service import push
from test_push import Browser


def _acct(client, alex, handle, kind, role):
    r = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": kind, "role": role},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    acct = r.json()
    tok = client.post(
        f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    return acct, {"Authorization": f"Bearer {tok['token']}"}


def _share(client, alex, account_id, section_id):
    r = client.put(
        f"{API}/shares",
        json={
            "account_id": account_id,
            "scope_kind": "section",
            "scope_id": section_id,
            "level": "edit",
        },
        headers=alex,
    )
    assert r.status_code == 200, r.text


def _section(client, alex, name="Project"):
    sec = client.post(
        f"{API}/sections", json={"name": name, "audience": "work"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    return sec


def _post(client, h, key, body, **extra):
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _bodies(client, h, key):
    r = client.get(f"{API}/channels/{key}/messages", params={"limit": 50}, headers=h)
    assert r.status_code == 200, r.text
    return [m["body_md"] for m in r.json()]


@pytest.fixture
def project(client, alex):
    sec = _section(client, alex)
    agent, ah = _acct(client, alex, "my-agent", "agent", "member")
    guest, gh = _acct(client, alex, "guest", "person", "guest")
    _share(client, alex, agent["id"], sec["id"])
    _share(client, alex, guest["id"], sec["id"])
    return {"sid": sec["id"], "key": f"section:{sec['id']}", "agent": ah, "guest": (guest, gh)}


def test_the_renudge_counts_only_what_the_guest_can_read(client, alex, db, project):
    key, ah = project["key"], project["agent"]
    _, gh = project["guest"]
    client.post(f"{API}/push/subscriptions", json=Browser().body(), headers=gh)
    client.patch(f"{API}/me/prefs", json={"push_reasons": ["chat"]}, headers=gh)
    _post(client, alex, key, "@my-agent please check the ledger")  # members-only
    _post(client, ah, key, "Ledger checked")  # members-only
    _post(client, alex, key, "Morning all")
    assert _bodies(client, gh, key) == ["Morning all"]
    received = []
    sent = push.renudge(
        db,
        lambda *args: (received.append(args[3]) or 201, ""),
        {},
        now=utcnow() + timedelta(minutes=11),
    )
    assert sent == 1, received
    assert received[0]["body"] == "1 message from Alex, waiting since you last looked"


def test_no_renudge_when_everything_unread_is_hidden_from_the_guest(client, alex, db, project):
    key, ah = project["key"], project["agent"]
    _, gh = project["guest"]
    client.post(f"{API}/push/subscriptions", json=Browser().body(), headers=gh)
    client.patch(f"{API}/me/prefs", json={"push_reasons": ["chat"]}, headers=gh)
    _post(client, alex, key, "@my-agent a members-only question")
    _post(client, ah, key, "A members-only answer")
    received = []
    push.renudge(
        db,
        lambda *args: (received.append(args[3]) or 201, ""),
        {},
        now=utcnow() + timedelta(minutes=11),
    )
    assert received == []
