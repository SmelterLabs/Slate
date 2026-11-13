"""Who counts as an admin, and what members can see (service/access.py).

One rule answers "is this caller an admin": an owner or admin account, through a token that
carries the `admin` scope and is not limited to some lists. It holds whether or not anything
in the workspace is private, and for every door (REST, MCP, the live gate).
"""

from __future__ import annotations

import base64

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


# --- Personal events --------------------------------------------------------------------------

# Push keys are only length-checked: an uncompressed point (65 bytes) and 16 bytes.
P256 = base64.urlsafe_b64encode(b"\x04" + b"k" * 64).rstrip(b"=").decode()
PUSH_AUTH = base64.urlsafe_b64encode(b"a" * 16).rstrip(b"=").decode()
PERSONAL = ("account.prefs_changed", "push.", "webhook.", "token.", "favorite.", "inbox.")


def _personal_history(client, alex, world) -> None:
    """alex changes prefs, adds a device, a webhook and a favorite, and reads the inbox."""
    me = _me(client, alex)
    r = client.patch(f"{API}/me/prefs", json={"email_to": "alex@example.com"}, headers=alex)
    assert r.status_code == 200, r.text
    r = client.post(
        f"{API}/push/subscriptions",
        json={
            "endpoint": "https://push.example.com/send/abc",
            "keys": {"p256dh": P256, "auth": PUSH_AUTH},
            "label": "Alex phone",
        },
        headers=alex,
    )
    assert r.status_code in (200, 201), r.text
    r = client.post(
        f"{API}/webhooks",
        json={"url": "https://rx.example.com/hook?key=K-123", "events": ["item.due"]},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    hook = r.json()["id"]
    r = client.patch(f"{API}/webhooks/{hook}", json={"disabled": True}, headers=alex)
    assert r.status_code == 200, r.text
    fav = client.post(
        f"{API}/favorites", json={"kind": "list", "target_id": world["todo"]["id"]}, headers=alex
    )
    assert fav.status_code in (200, 201), fav.text
    assert client.delete(f"{API}/favorites/{fav.json()['id']}", headers=alex).status_code == 204
    tok = client.post(f"{API}/accounts/{me}/tokens", json={"scopes": ["read"]}, headers=alex)
    assert client.delete(f"{API}/tokens/{tok.json()['id']}", headers=alex).status_code == 204
    r = client.post(f"{API}/inbox/read", json={"all": True}, headers=alex)
    assert r.status_code == 200, r.text


def _about(e: dict, account_id: str) -> bool:
    snaps = [s for s in (e.get("before"), e.get("after")) if isinstance(s, dict)]
    return e["subject_id"] == account_id or any(s.get("account_id") == account_id for s in snaps)


def _personal(events: list[dict], who: str, viewer: str | None = None) -> list[str]:
    """Personal event types `who` caused, leaving out those about `viewer` itself."""
    return sorted(
        {
            e["type"]
            for e in events
            if e["type"].startswith(PERSONAL)
            and (e.get("actor_id") or (e.get("actor") or {}).get("id")) == who
            and not (viewer and _about(e, viewer))
        }
    )


def _gate_passes(db, account_id: str, who: str) -> list[str]:
    from sqlalchemy import select

    from slate.models import Event
    from slate.service import access
    from slate.service.core import event_payload

    gate = access.EventGate(account_id)
    gate.load(db)
    evs = [event_payload(e) for e in db.scalars(select(Event).order_by(Event.id))]
    return sorted(
        {
            e["type"]
            for e in evs
            if e["type"].startswith(PERSONAL)
            and e["actor_id"] == who
            and not (account_id != who and _about(e, account_id))
            and gate.allows(db, e)
        }
    )


def test_member_never_reads_another_accounts_personal_events(client, alex, world, db):
    me = _me(client, alex)
    _personal_history(client, alex, world)
    member, mh = world["account"]("member2", "member", scopes=("read",))
    # Nothing is private: the member's access is unrestricted, and still none of it shows.
    for path in ("/events", "/audit"):
        r = client.get(f"{API}{path}", params={"limit": 500}, headers=mh)
        assert r.status_code == 200, r.text
        rows = r.json().get("events") or r.json().get("entries")
        assert _personal(rows, me, member["id"]) == [], path
    assert "K-123" not in client.get(f"{API}/events", params={"limit": 500}, headers=mh).text
    assert _gate_passes(db, member["id"], me) == []
    # The account itself and an admin see them.
    own = client.get(f"{API}/events", params={"limit": 500}, headers=alex).json()["events"]
    assert {"account.prefs_changed", "push.subscribed", "webhook.created"} <= set(
        _personal(own, me)
    )
    assert "webhook.disabled" in _gate_passes(db, me, me)
    # Once something is private the member is restricted: the same rule, the same answer.
    client.patch(f"{API}/sections/{world['work']['id']}", json={"private": True}, headers=alex)
    rows = client.get(f"{API}/events", params={"limit": 500}, headers=mh).json()["events"]
    assert _personal(rows, me, member["id"]) == []
    db.expire_all()
    assert _gate_passes(db, member["id"], me) == []


def test_member_sees_its_own_personal_events(client, alex, world):
    member, mh = world["account"]("member2", "member")
    r = client.patch(f"{API}/me/prefs", json={"email_to": "member@example.com"}, headers=mh)
    assert r.status_code == 200, r.text
    rows = client.get(f"{API}/events", params={"limit": 500}, headers=mh).json()["events"]
    assert "account.prefs_changed" in _personal(rows, member["id"])


def test_restricted_member_replay_matches_the_live_gate_for_tags_and_accounts(
    client, alex, world, db
):
    from slate.service import access
    from slate.service.core import event_payload

    client.patch(f"{API}/sections/{world['work']['id']}", json={"private": True}, headers=alex)
    member, mh = world["account"]("member2", "member")
    assert client.post(f"{API}/tags", json={"name": "fresh"}, headers=alex).status_code == 201
    world["account"]("newcomer", "member")
    rows = client.get(f"{API}/events", params={"limit": 500}, headers=mh).json()["events"]
    replay = {e["id"] for e in rows if e["type"].startswith(("tag.", "account."))}

    from sqlalchemy import select

    from slate.models import Event

    gate = access.EventGate(member["id"])
    gate.load(db)
    live = {
        e["id"]
        for e in map(event_payload, db.scalars(select(Event).order_by(Event.id)))
        if e["type"].startswith(("tag.", "account.")) and gate.allows(db, e)
    }
    assert replay == live
    assert any(e["type"] == "tag.created" for e in rows)
    assert sum(e["type"] == "account.created" for e in rows) >= 3


# --- Accounts ---------------------------------------------------------------------------------


def test_the_system_handle_is_reserved_and_takes_no_token(client, alex, db):
    from slate.service.due import system_actor

    r = client.post(
        f"{API}/accounts",
        json={"handle": "slate", "display_name": "Fake", "kind": "agent"},
        headers=alex,
    )
    assert r.status_code == 422, r.text
    system = system_actor(db).account_id
    db.commit()
    r = client.post(f"{API}/accounts/{system}/tokens", json={"scopes": ["write"]}, headers=alex)
    assert r.status_code == 422, r.text


def test_bootstrap_refuses_the_system_handle(client):
    r = client.post(f"{API}/bootstrap", json={"handle": "slate", "display_name": "Slate person"})
    assert r.status_code == 422, r.text


def test_bootstrap_still_works_after_slate_recorded_something(client, db):
    from slate.service.due import system_actor

    system_actor(db)  # a scheduler alarm before anyone has bootstrapped
    db.commit()
    r = client.post(f"{API}/bootstrap", json={"handle": "alex", "display_name": "Alex"})
    assert r.status_code == 201, r.text
    again = client.post(f"{API}/bootstrap", json={"handle": "alex2", "display_name": "Alex 2"})
    assert again.status_code == 410


def test_an_admin_cannot_act_as_disable_or_rename_another_admin_or_the_owner(client, alex, world):
    owner = _me(client, alex)
    admin, ah = world["account"]("admin2", "admin", scopes=("admin",))
    other, oh = world["account"]("admin3", "admin", scopes=("admin",))
    member, _ = world["account"]("member2", "member")
    tok = client.post(
        f"{API}/accounts/{other['id']}/tokens", json={"scopes": ["read"]}, headers=alex
    )
    for path, body in (
        (f"/accounts/{other['id']}/tokens", {"scopes": ["write"]}),
        (f"/accounts/{owner}/tokens", {"scopes": ["write"]}),
    ):
        assert client.post(f"{API}{path}", json=body, headers=ah).status_code == 403, path
    for target in (other["id"], owner):
        for body in ({"disabled": True}, {"display_name": "Renamed"}):
            r = client.patch(f"{API}/accounts/{target}", json=body, headers=ah)
            assert r.status_code == 403, (target, body, r.text)
    assert client.delete(f"{API}/tokens/{tok.json()['id']}", headers=ah).status_code == 403
    # An admin still manages members, and itself; the owner manages admins.
    assert (
        client.patch(
            f"{API}/accounts/{member['id']}", json={"display_name": "M"}, headers=ah
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{API}/accounts/{member['id']}/tokens", json={"scopes": ["write"]}, headers=ah
        ).status_code
        == 201
    )
    assert (
        client.patch(
            f"{API}/accounts/{admin['id']}", json={"display_name": "Me"}, headers=ah
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"{API}/accounts/{other['id']}", json={"disabled": True}, headers=alex
        ).status_code
        == 200
    )


# --- Deny-list, automations, doc threads ------------------------------------------------------


def test_default_agent_rules_stop_an_agent_deleting_an_attachment_for_good(client, alex, world):
    _, bot = world["account"]("bot", "member", kind="agent")
    item = world["milk"]["id"]
    r = client.post(
        f"{API}/items/{item}/attachments",
        params={"filename": "notes.txt"},
        content=b"keep me",
        headers={**alex, "Content-Type": "text/plain"},
    )
    assert r.status_code == 201, r.text
    att = r.json()["id"]
    r = client.delete(f"{API}/attachments/{att}", headers=bot)
    assert r.status_code == 403, r.text
    assert client.delete(f"{API}/attachments/{att}", headers=alex).status_code == 204


def test_an_automation_cannot_move_tasks_into_a_list_its_author_cannot_edit(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["todo"]["id"], "edit")
    share(client, alex, guest["id"], "list", world["clients"]["id"], "view")
    rule = {
        "name": "push along",
        "list_id": world["todo"]["id"],
        "trigger": {"type": "task_created"},
        "actions": [{"type": "move_list", "list_id": world["clients"]["id"]}],
    }
    r = client.post(f"{API}/automations", json=rule, headers=gh)
    assert r.status_code == 403, r.text
    ok = {**rule, "actions": [{"type": "set_priority", "priority": "high"}]}
    r = client.post(f"{API}/automations", json=ok, headers=gh)
    assert r.status_code == 201, r.text
    r = client.patch(
        f"{API}/automations/{r.json()['id']}", json={"actions": rule["actions"]}, headers=gh
    )
    assert r.status_code == 403, r.text
    # Into a list it may edit, the same rule is fine.
    share(client, alex, guest["id"], "list", world["clients"]["id"], "edit")
    assert client.post(f"{API}/automations", json=rule, headers=gh).status_code == 201


def _ask_on_page(client, alex, world, assignee="alex"):
    page = world["post"](
        "/docs", {"title": "p", "body_md": "the price is 10\n", "list_id": world["todo"]["id"]}
    )
    return world["post"](
        f"/docs/{page['id']}/comments",
        {"body_md": "check this", "anchor": {"quote": "price"}, "assignee": assignee},
    )


def test_comment_level_caller_cannot_resolve_or_reopen_someone_elses_thread(client, alex, world):
    guest, gh = world["account"]("guest", "guest")
    share(client, alex, guest["id"], "list", world["todo"]["id"], "comment")
    c = _ask_on_page(client, alex, world)
    assert client.post(f"{API}/doc-comments/{c['id']}/resolve", headers=gh).status_code == 403
    assert client.post(f"{API}/doc-comments/{c['id']}/resolve", headers=alex).status_code == 200
    assert client.post(f"{API}/doc-comments/{c['id']}/reopen", headers=gh).status_code == 403
    # Edit on the doc is enough, and so is being the Ask's assignee.
    share(client, alex, guest["id"], "list", world["todo"]["id"], "edit")
    assert client.post(f"{API}/doc-comments/{c['id']}/reopen", headers=gh).status_code == 200
    share(client, alex, guest["id"], "list", world["todo"]["id"], "comment")
    asked = _ask_on_page(client, alex, world, assignee="guest")
    assert client.post(f"{API}/doc-comments/{asked['id']}/resolve", headers=gh).status_code == 200
