"""An invitation link never gives back access an admin took away: removing a guest's section
share or cancelling an invitation retires the guest's unused links for that section, and
accepting a link never changes a share the admin already set."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from slate.app import create_app
from test_collaboration import API, accept, invite


@pytest.fixture(autouse=True)
def fresh_attempts(monkeypatch):
    from slate.api import collaboration

    monkeypatch.setattr(collaboration, "attempts", collaboration.Attempts())


def _shares(client, alex, account_id):
    r = client.get(f"{API}/shares", params={"account_id": account_id}, headers=alex)
    assert r.status_code == 200, r.text
    return r.json()


def _device(invitation):
    browser = TestClient(create_app())
    return browser, accept(browser, invitation)


def test_removing_the_share_retires_unused_links(client, alex, todo):
    first = invite(client, alex, todo)
    phone, r = _device(first)
    assert r.status_code == 200, r.text
    second = client.post(f"{API}/invitations/{first['id']}/reissue", headers=alex).json()
    for share in _shares(client, alex, first["account_id"]):
        assert client.delete(f"{API}/shares/{share['id']}", headers=alex).status_code == 204
    laptop, r = _device(second)
    assert r.status_code == 410, r.text
    assert _shares(client, alex, first["account_id"]) == []


def test_revoking_the_share_by_level_none_retires_unused_links(client, alex, todo):
    first = invite(client, alex, todo)
    r = client.put(
        f"{API}/shares",
        json={
            "account_id": first["account_id"],
            "scope_kind": "section",
            "scope_id": todo["section_id"],
            "level": None,
        },
        headers=alex,
    )
    assert r.status_code in (200, 204), r.text
    _, r = _device(first)
    assert r.status_code == 410, r.text
    assert _shares(client, alex, first["account_id"]) == []


def test_cancelling_an_invitation_retires_its_sibling_links(client, alex, todo):
    first = invite(client, alex, todo)
    phone, r = _device(first)
    assert r.status_code == 200
    sibling = client.post(f"{API}/invitations/{first['id']}/reissue", headers=alex).json()
    assert client.delete(f"{API}/invitations/{first['id']}", headers=alex).status_code == 204
    assert phone.get(f"{API}/me").status_code == 401
    laptop, r = _device(sibling)
    assert r.status_code == 410, r.text
    rows = client.get(f"{API}/invitations", params={"section_id": todo["section_id"]}, headers=alex)
    assert all(row["revoked_at"] for row in rows.json())


def test_accepting_never_raises_a_share_the_admin_lowered(client, alex, todo):
    first = invite(client, alex, todo)
    r = client.put(
        f"{API}/shares",
        json={
            "account_id": first["account_id"],
            "scope_kind": "section",
            "scope_id": todo["section_id"],
            "level": "view",
        },
        headers=alex,
    )
    assert r.status_code == 200, r.text
    _, r = _device(first)
    assert r.status_code == 200, r.text
    assert [s["level"] for s in _shares(client, alex, first["account_id"])] == ["view"]


def test_a_link_from_an_inviter_who_lost_admin_no_longer_works(client, alex, todo):
    other = client.post(
        f"{API}/accounts",
        json={"handle": "other-admin", "display_name": "Other", "kind": "person", "role": "admin"},
        headers=alex,
    ).json()
    tok = client.post(
        f"{API}/accounts/{other['id']}/tokens", json={"scopes": ["admin"]}, headers=alex
    ).json()
    oh = {"Authorization": f"Bearer {tok['token']}"}
    client.patch(f"{API}/sections/{todo['section_id']}", json={"private": True}, headers=alex)
    r = client.post(
        f"{API}/invitations",
        headers=oh,
        json={"section_id": todo["section_id"], "handle": "guest", "display_name": "Guest"},
    )
    assert r.status_code == 201, r.text
    link = r.json()
    assert (
        client.put(
            f"{API}/accounts/{other['id']}/role", json={"role": "member"}, headers=alex
        ).status_code
        == 200
    )
    _, r = _device(link)
    assert r.status_code == 410, r.text
    assert _shares(client, alex, link["account_id"]) == []


def test_a_fresh_link_after_removal_still_works(client, alex, todo):
    first = invite(client, alex, todo)
    _, r = _device(first)
    assert r.status_code == 200
    for share in _shares(client, alex, first["account_id"]):
        client.delete(f"{API}/shares/{share['id']}", headers=alex)
    again = client.post(
        f"{API}/invitations",
        headers=alex,
        json={"section_id": todo["section_id"], "handle": "guest", "display_name": "Guest"},
    )
    assert again.status_code == 201, again.text
    _, r = _device(again.json())
    assert r.status_code == 200, r.text
    assert [s["level"] for s in _shares(client, alex, first["account_id"])] == ["edit"]
