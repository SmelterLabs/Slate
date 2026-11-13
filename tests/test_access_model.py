"""Who counts as an admin, and what members can see (service/access.py).

One rule answers "is this caller an admin": an owner or admin account, through a token that
carries the `admin` scope and is not limited to some lists. It holds whether or not anything
in the workspace is private, and for every door (REST, MCP, the live gate).
"""

from __future__ import annotations

import test_permissions
from test_permissions import share  # noqa: F401

world = test_permissions.world

API = "/api/v1"


def _me(client, h) -> str:
    return client.get(f"{API}/me", headers=h).json()["account"]["id"]


def _token(client, alex, account_id, scopes=("admin",), limit=None) -> dict[str, str]:
    body = {"scopes": list(scopes)}
    if limit:
        body["limit"] = limit
    r = client.post(f"{API}/accounts/{account_id}/tokens", json=body, headers=alex)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- One admin rule ---------------------------------------------------------------------------


def test_member_cannot_rename_merge_or_delete_tags_when_nothing_is_private(client, alex, world):
    _, mh = world["account"]("member2", "member")
    _, bot = world["account"]("bot", "member", kind="agent")
    for name in ("alpha", "beta"):
        assert client.post(f"{API}/tags", json={"name": name}, headers=alex).status_code == 201
    for h in (mh, bot):
        r = client.patch(f"{API}/tags/alpha", json={"name": "renamed"}, headers=h)
        assert r.status_code == 403, r.text
        r = client.post(f"{API}/tags/alpha/merge", json={"into": "beta"}, headers=h)
        assert r.status_code == 403, r.text
        assert client.delete(f"{API}/tags/alpha", headers=h).status_code == 403
    # Nothing private changes nothing: the same refusal once a section is private.
    client.patch(f"{API}/sections/{world['work']['id']}", json={"private": True}, headers=alex)
    assert client.delete(f"{API}/tags/alpha", headers=mh).status_code == 403
    assert client.delete(f"{API}/tags/alpha", headers=alex).status_code == 204


def test_admin_needs_an_unlimited_admin_token_for_admin_only_writes(client, alex, world):
    me = _me(client, alex)
    assert client.post(f"{API}/tags", json={"name": "alpha"}, headers=alex).status_code == 201
    limited = _token(client, alex, me, limit={"list_ids": [world["todo"]["id"]]})
    write_only = _token(client, alex, me, scopes=("write",))
    for h in (limited, write_only):
        r = client.patch(f"{API}/tags/alpha", json={"color": "#ff0000"}, headers=h)
        assert r.status_code == 403, r.text
        assert client.get(f"{API}/me", headers=h).json()["access"]["manage"] is False
    assert client.get(f"{API}/me", headers=alex).json()["access"]["manage"] is True


def test_list_limited_admin_token_is_not_an_admin_for_card_comments(client, alex, world):
    member, mh = world["account"]("member2", "member")
    me = _me(client, alex)
    limited = _token(client, alex, me, limit={"list_ids": [world["todo"]["id"]]})
    item = world["milk"]["id"]
    r = client.post(
        f"{API}/items/{item}/comments", json={"body_md": "mine", "assignee": "alex"}, headers=mh
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert (
        client.patch(f"{API}/comments/{cid}", json={"assignee": None}, headers=limited).status_code
        == 403
    )
    assert client.delete(f"{API}/comments/{cid}", headers=limited).status_code == 403
    # Through the unlimited admin token the same account may.
    assert (
        client.patch(f"{API}/comments/{cid}", json={"assignee": None}, headers=alex).status_code
        == 200
    )
    assert client.delete(f"{API}/comments/{cid}", headers=alex).status_code == 204


def test_list_limited_admin_token_is_not_an_admin_for_doc_threads(client, alex, world):
    _, mh = world["account"]("member2", "member")
    me = _me(client, alex)
    limited = _token(client, alex, me, limit={"list_ids": [world["todo"]["id"]]})
    page = client.post(
        f"{API}/docs",
        json={"title": "p", "body_md": "the price is 10\n", "list_id": world["todo"]["id"]},
        headers=alex,
    ).json()
    c = client.post(
        f"{API}/docs/{page['id']}/comments",
        json={"body_md": "check", "anchor": {"quote": "price"}},
        headers=mh,
    ).json()
    url = f"{API}/doc-comments/{c['id']}/assign"
    r = client.post(url, json={"assignee": "alex"}, headers=limited)
    assert r.status_code == 403, r.text
    r = client.post(url, json={"assignee": "alex"}, headers=alex)
    assert r.status_code == 200, r.text


def test_capacity_of_another_account_needs_an_admin_token(client, alex, world):
    member, _ = world["account"]("member2", "member")
    me = _me(client, alex)
    write_only = _token(client, alex, me, scopes=("write",))
    url = f"{API}/accounts/{member['id']}/capacity"
    r = client.patch(url, json={"tasks_per_day": 3}, headers=write_only)
    assert r.status_code == 403, r.text
    assert client.patch(url, json={"tasks_per_day": 3}, headers=alex).status_code == 200
