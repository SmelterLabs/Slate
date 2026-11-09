"""Roles, sharing, private sections/lists, token limits and the audit log (service/access.py).

The setup: alex (owner) has two sections, Personal (Todo) and Work (Clients). A guest, `guest`,
gets lists shared with it; a member, `member2`, sees what is not private.
"""

from __future__ import annotations

import pytest

API = "/api/v1"


@pytest.fixture
def world(client, alex):
    def post(path, body, h=alex):
        r = client.post(f"{API}{path}", json=body, headers=h)
        assert r.status_code in (200, 201), r.text
        return r.json()

    personal = post("/sections", {"name": "Personal", "audience": "personal"})
    work = post("/sections", {"name": "Work", "audience": "work"})
    todo = post("/lists", {"section_id": personal["id"], "name": "Todo"})
    clients = post("/lists", {"section_id": work["id"], "name": "Clients"})
    milk = post("/items", {"list_id": todo["id"], "title": "Buy milk"})
    renewal = post("/items", {"list_id": clients["id"], "title": "Annual renewal"})

    def account(handle, role, kind="person", scopes=("write",), limit=None):
        a = post(
            "/accounts",
            {"handle": handle, "display_name": handle.title(), "kind": kind, "role": role},
        )
        body = {"scopes": list(scopes)}
        if limit:
            body["limit"] = limit
        tok = post(f"/accounts/{a['id']}/tokens", body)
        return a, {"Authorization": f"Bearer {tok['token']}"}

    return {
        "post": post,
        "account": account,
        "personal": personal,
        "work": work,
        "todo": todo,
        "clients": clients,
        "milk": milk,
        "renewal": renewal,
    }


def share(client, alex, account_id, kind, scope_id, level):
    r = client.put(
        f"{API}/shares",
        json={"account_id": account_id, "scope_kind": kind, "scope_id": scope_id, "level": level},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    return r.json()


def titles(client, h, **params):
    r = client.get(f"{API}/items", params=params, headers=h)
    assert r.status_code == 200, r.text
    return sorted(i["title"] for i in r.json())


def test_bootstrap_account_is_owner_and_sees_everything(client, alex, world):
    me = client.get(f"{API}/me", headers=alex).json()
    assert me["account"]["role"] == "owner"
    assert me["access"]["all"] is True and me["access"]["manage"] is True
    assert titles(client, alex) == ["Annual renewal", "Buy milk"]


def test_guest_sees_nothing_until_shared(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    assert titles(client, gh) == []
    assert client.get(f"{API}/sections", headers=gh).json() == []
    assert client.get(f"{API}/lists", headers=gh).json() == []
    assert client.get(f"{API}/items/{world['milk']['id']}", headers=gh).status_code == 404

    share(client, alex, guest["id"], "list", world["clients"]["id"], "edit")
    assert titles(client, gh) == ["Annual renewal"]
    assert [s["name"] for s in client.get(f"{API}/sections", headers=gh).json()] == ["Work"]
    assert [x["name"] for x in client.get(f"{API}/lists", headers=gh).json()] == ["Clients"]
    me = client.get(f"{API}/me", headers=gh).json()
    assert me["access"]["all"] is False
    assert me["access"]["lists"] == {world["clients"]["id"]: "edit"}
    assert me["access"]["general"] is False


def test_guest_cannot_write_outside_shares(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "edit")
    r = client.post(f"{API}/items", json={"list_id": world["todo"]["id"], "title": "x"}, headers=gh)
    assert r.status_code in (403, 404)
    r = client.patch(f"{API}/items/{world['milk']['id']}", json={"title": "y"}, headers=gh)
    assert r.status_code == 404
    # Inside the share: fine.
    r = client.patch(
        f"{API}/items/{world['renewal']['id']}", json={"title": "Renewal 2027"}, headers=gh
    )
    assert r.status_code == 200, r.text
    # Moving the shared task into a list the guest cannot see is refused.
    r = client.patch(
        f"{API}/items/{world['renewal']['id']}", json={"list_id": world["todo"]["id"]}, headers=gh
    )
    assert r.status_code in (403, 404, 422)
    assert (
        client.post(
            f"{API}/sections", json={"name": "Mine", "audience": "personal"}, headers=gh
        ).status_code
        == 403
    )


def test_view_and_comment_levels(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    renewal = world["renewal"]["id"]
    share(client, alex, guest["id"], "list", world["clients"]["id"], "view")
    assert client.get(f"{API}/items/{renewal}", headers=gh).status_code == 200
    assert (
        client.patch(f"{API}/items/{renewal}", json={"title": "z"}, headers=gh).status_code == 403
    )
    r = client.post(f"{API}/items/{renewal}/comments", json={"body_md": "hi"}, headers=gh)
    assert r.status_code == 403
    # Watching is personal: allowed at view.
    assert client.post(f"{API}/items/{renewal}/watch", headers=gh).status_code == 200

    share(client, alex, guest["id"], "list", world["clients"]["id"], "comment")
    r = client.post(f"{API}/items/{renewal}/comments", json={"body_md": "hi"}, headers=gh)
    assert r.status_code == 201, r.text
    assert (
        client.patch(f"{API}/items/{renewal}", json={"title": "z"}, headers=gh).status_code == 403
    )


def test_section_share_covers_its_lists_but_not_private_ones(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    secret = world["post"]("/lists", {"section_id": world["work"]["id"], "name": "Secret"})
    world["post"]("/items", {"list_id": secret["id"], "title": "Hidden"})
    r = client.patch(f"{API}/lists/{secret['id']}", json={"private": True}, headers=alex)
    assert r.status_code == 200 and r.json()["private"] is True
    share(client, alex, guest["id"], "section", world["work"]["id"], "view")
    assert titles(client, gh) == ["Annual renewal"]
    share(client, alex, guest["id"], "list", secret["id"], "view")
    assert titles(client, gh) == ["Annual renewal", "Hidden"]


def test_member_sees_all_but_private(client, alex, world):
    member2, mh = world["account"]("member2", "member")
    assert titles(client, mh) == ["Annual renewal", "Buy milk"]
    r = client.patch(
        f"{API}/sections/{world['personal']['id']}", json={"private": True}, headers=alex
    )
    assert r.status_code == 200, r.text
    assert titles(client, mh) == ["Annual renewal"]
    # A member cannot make things private or share.
    r = client.patch(f"{API}/sections/{world['work']['id']}", json={"private": True}, headers=mh)
    assert r.status_code == 403
    r = client.put(
        f"{API}/shares",
        json={
            "account_id": member2["id"],
            "scope_kind": "list",
            "scope_id": world["todo"]["id"],
            "level": "edit",
        },
        headers=mh,
    )
    assert r.status_code == 403
    share(client, alex, member2["id"], "list", world["todo"]["id"], "comment")
    assert titles(client, mh) == ["Annual renewal", "Buy milk"]


def test_member_admin_token_is_capped(client, alex, world):
    _, mh = world["account"]("member2", "member", scopes=("admin",))
    me = client.get(f"{API}/me", headers=mh).json()
    assert "admin" not in me["scopes"]
    assert client.get(f"{API}/members", headers=mh).status_code == 403


def test_list_limited_agent_token(client, alex, world):
    bot, bh = world["account"](
        "bot", "member", kind="agent", limit={"list_ids": [world["todo"]["id"]], "level": "comment"}
    )
    assert titles(client, bh) == ["Buy milk"]
    milk = world["milk"]["id"]
    assert client.patch(f"{API}/items/{milk}", json={"title": "x"}, headers=bh).status_code == 403
    r = client.post(f"{API}/items/{milk}/comments", json={"body_md": "on it"}, headers=bh)
    assert r.status_code == 201
    tokens = client.get(f"{API}/accounts/{bot['id']}/tokens", headers=alex).json()
    assert tokens[0]["limit"] == {"list_ids": [world["todo"]["id"]], "level": "comment"}
    # A limited token never reaches beyond its account: a guest's limit cannot add lists.
    guest, _ = world["account"]("guest", "guest")
    tok = world["post"](
        f"/accounts/{guest['id']}/tokens",
        {"scopes": ["write"], "limit": {"list_ids": [world["todo"]["id"]]}},
    )
    gh = {"Authorization": f"Bearer {tok['token']}"}
    assert titles(client, gh) == []


def test_checkin_today_export_and_events_are_filtered(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "edit")
    export = client.get(f"{API}/export", headers=gh)
    assert export.status_code == 200
    assert "Buy milk" not in export.text and "Annual renewal" in export.text
    events = client.get(f"{API}/events", params={"since": "0", "limit": 1000}, headers=gh).json()
    assert all(e["item_id"] != world["milk"]["id"] for e in events["events"])
    assert any(e["item_id"] == world["renewal"]["id"] for e in events["events"])
    assert client.get(f"{API}/checkin", headers=gh).status_code == 200
    r = client.get(f"{API}/items/{world['milk']['id']}/comments", headers=gh)
    assert r.status_code == 404


def test_chat_channels_follow_access(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "comment")
    keys = {c["key"] for c in client.get(f"{API}/channels", headers=gh).json()}
    assert f"list:{world['clients']['id']}" in keys
    assert f"list:{world['todo']['id']}" not in keys
    assert "general" not in keys
    r = client.post(
        f"{API}/channels/list:{world['todo']['id']}/messages", json={"body_md": "hi"}, headers=gh
    )
    assert r.status_code in (403, 404)
    r = client.post(
        f"{API}/channels/list:{world['clients']['id']}/messages",
        json={"body_md": "hi"},
        headers=gh,
    )
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/channels/general/messages", json={"body_md": "hi"}, headers=gh)
    assert r.status_code in (403, 404)


def test_roles_only_owner_makes_admins(client, alex, world):
    member, ah = world["account"]("member", "member")
    member2, _ = world["account"]("member2", "member")
    r = client.put(f"{API}/accounts/{member['id']}/role", json={"role": "admin"}, headers=alex)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    # The member's token now carries admin (it was minted with write: the role caps, not grants).
    tok = world["post"](f"/accounts/{member['id']}/tokens", {"scopes": ["admin"]})
    ah = {"Authorization": f"Bearer {tok['token']}"}
    assert client.get(f"{API}/members", headers=ah).status_code == 200
    r = client.put(f"{API}/accounts/{member2['id']}/role", json={"role": "guest"}, headers=ah)
    assert r.status_code == 200
    r = client.put(f"{API}/accounts/{member2['id']}/role", json={"role": "admin"}, headers=ah)
    assert r.status_code == 403
    me = client.get(f"{API}/me", headers=alex).json()["account"]
    r = client.put(f"{API}/accounts/{me['id']}/role", json={"role": "member"}, headers=ah)
    assert r.status_code == 403
    r = client.put(f"{API}/accounts/{me['id']}/role", json={"role": "member"}, headers=alex)
    assert r.status_code == 422  # not your own


def test_hand_ownership_over(client, alex, world):
    member, _ = world["account"]("member", "member")
    r = client.put(f"{API}/accounts/{member['id']}/role", json={"role": "owner"}, headers=alex)
    assert r.status_code == 200
    roles = {
        m["account"]["handle"]: m["role"] for m in client.get(f"{API}/members", headers=alex).json()
    }
    assert roles["member"] == "owner" and roles["alex"] == "admin"


def test_who_can_see_a_list(client, alex, world):
    guest, _ = world["account"]("guest", "guest")
    world["account"]("member2", "member")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "view")
    rows = client.get(f"{API}/lists/{world['clients']['id']}/access", headers=alex).json()
    by = {r["account_id"]: r for r in rows}
    assert by[guest["id"]]["via"] == "share" and by[guest["id"]]["level"] == "view"
    assert len(rows) == 3  # alex (role), member2 (member default), guest (share)
    eff = client.get(f"{API}/accounts/{guest['id']}/access", headers=alex).json()
    assert eff["lists"] == {world["clients"]["id"]: "view"}


def test_audit_log_filters_and_csv(client, alex, world):
    bot, bh = world["account"]("bot", "member", kind="agent")
    client.patch(f"{API}/items/{world['milk']['id']}", json={"title": "Oat milk"}, headers=bh)
    page = client.get(f"{API}/audit", params={"actor_kind": "agent"}, headers=alex).json()
    assert page["entries"] and all(e["actor"]["kind"] == "agent" for e in page["entries"])
    assert page["entries"][0]["item"]["title"] == "Oat milk"
    by_list = client.get(
        f"{API}/audit", params={"list_id": world["clients"]["id"]}, headers=alex
    ).json()["entries"]
    assert by_list and all(e["item_id"] in (None, world["renewal"]["id"]) for e in by_list)
    paged = client.get(f"{API}/audit", params={"limit": 2}, headers=alex).json()
    assert paged["has_more"] and paged["next_before"]
    older = client.get(
        f"{API}/audit", params={"limit": 2, "before": paged["next_before"]}, headers=alex
    ).json()
    assert older["entries"][0]["id"] < paged["entries"][-1]["id"]
    csv = client.get(f"{API}/audit.csv", params={"actor": "bot"}, headers=alex)
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("id,at,actor") and len(lines) >= 2 and "bot" in lines[1]
    facets = client.get(f"{API}/audit/facets", headers=alex).json()
    assert "item.updated" in facets["types"]


def test_guest_audit_sees_only_shared(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "view")
    entries = client.get(f"{API}/audit", params={"limit": 500}, headers=gh).json()["entries"]
    assert entries
    assert all(e["item_id"] != world["milk"]["id"] for e in entries)
    assert not any(
        e["type"] == "account.created" and e["subject_id"] != guest["id"] for e in entries
    )


def test_mcp_honours_access(client, alex, world, db):
    """The MCP backend binds the same access: a guest's item search is filtered."""
    from slate.mcp_server import LocalBackend
    from slate.mcp_tools import TOOLS

    guest, gh = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "view")
    token = gh["Authorization"].split()[1]
    spec = TOOLS["slate_item"]
    action = spec.actions["list"]
    params = action.params.model_validate({})
    out = LocalBackend(token)._run(spec, "list", params, None)
    text = str(out)
    assert "Annual renewal" in text and "Buy milk" not in text


def test_two_guests_never_share_a_cached_filter(client, alex, world):
    """The read filter's values are bound per request; the compiled-statement cache must not
    leak one caller's lists to another."""
    guest, gh = world["account"]("guest", "guest")
    guest2, uh = world["account"]("guest2", "guest")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "view")
    share(client, alex, guest2["id"], "list", world["todo"]["id"], "view")
    for _ in range(3):
        assert titles(client, gh) == ["Annual renewal"]
        assert titles(client, uh) == ["Buy milk"]
        assert titles(client, alex) == ["Annual renewal", "Buy milk"]


def test_stream_gate_drops_unseen_events(client, alex, world, db):
    from sqlalchemy import select

    from slate.models import Event
    from slate.service import access
    from slate.service.core import event_payload

    guest, _ = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "view")
    gate = access.EventGate(guest["id"])
    gate.load(db)
    evs = [event_payload(e) for e in db.scalars(select(Event).order_by(Event.id))]
    seen = {e["item_id"] for e in evs if e["item_id"] and gate.allows(db, e)}
    assert seen == {world["renewal"]["id"]}
    # Owner: no gate at all.
    owner = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    g2 = access.EventGate(owner)
    assert g2.load(db) is None


def test_docs_follow_their_top_page_place(client, alex, world, db):
    """A page is seen where its top page lives; subpages inherit it; a guest only reaches docs
    in what is shared with them, and only edits at edit."""
    from sqlalchemy import select

    from slate.models import Event
    from slate.service import access
    from slate.service.core import event_payload

    post = world["post"]
    in_clients = post("/docs", {"title": "Account notes", "list_id": world["clients"]["id"]})
    sub = post("/docs", {"title": "Account call", "parent_id": in_clients["id"]})
    in_personal = post("/docs", {"title": "Diary", "section_id": world["personal"]["id"]})
    deep = post("/docs", {"title": "Diary page", "parent_id": in_personal["id"]})
    wiki = post("/docs", {"title": "Wiki", "body_md": "hello"})

    guest, gh = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "comment")

    def seen(h):
        r = client.get(f"{API}/docs", headers=h)
        assert r.status_code == 200, r.text
        return sorted(d["title"] for d in r.json())

    assert seen(gh) == ["Account call", "Account notes"]
    for d in (in_personal, deep, wiki):
        assert client.get(f"{API}/docs/{d['id']}", headers=gh).status_code == 404
        assert client.get(f"{API}/docs/{d['id']}/versions", headers=gh).status_code == 404
    assert client.get(f"{API}/docs/{sub['id']}", headers=gh).status_code == 200
    # Comment is not enough to edit, create or move a page.
    r = client.patch(f"{API}/docs/{sub['id']}", json={"body_md": "x"}, headers=gh)
    assert r.status_code == 403, r.text
    r = client.post(f"{API}/docs", json={"title": "Mine", "parent_id": sub["id"]}, headers=gh)
    assert r.status_code == 403, r.text
    # Edit allows it, but never moving it out to the workspace (guests have no workspace).
    share(client, alex, guest["id"], "list", world["clients"]["id"], "edit")
    r = client.patch(f"{API}/docs/{sub['id']}", json={"body_md": "edited"}, headers=gh)
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/docs/{sub['id']}/move", json={}, headers=gh)
    assert r.status_code == 403, r.text
    # A member sees the workspace; a private section's docs drop out for them.
    member2, mh = world["account"]("member2", "member")
    assert seen(mh) == ["Account call", "Account notes", "Diary", "Diary page", "Wiki"]
    client.patch(f"{API}/sections/{world['personal']['id']}", json={"private": True}, headers=alex)
    assert seen(mh) == ["Account call", "Account notes", "Wiki"]
    r = client.patch(f"{API}/docs/{deep['id']}", json={"body_md": "x"}, headers=mh)
    assert r.status_code == 404, r.text
    # The live stream: the guest hears only about the Clients pages.
    gate = access.EventGate(guest["id"])
    gate.load(db)
    evs = [event_payload(e) for e in db.scalars(select(Event).where(Event.type.like("doc.%")))]
    heard = {e["subject_id"] for e in evs if gate.allows(db, e)}
    assert heard == {in_clients["id"], sub["id"]}
