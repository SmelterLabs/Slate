"""The agent action deny-list (service/agent_rules.py): what agents may never do, whatever token."""

from __future__ import annotations

from sqlalchemy import select

from conftest import API
from slate.db import SessionLocal
from slate.models import Event
from slate.service import agent_rules
from test_mcp import call, err, http_mcp, ok


def _bearer(client, alex, account_id, scopes):
    tok = client.post(
        f"{API}/accounts/{account_id}/tokens", json={"scopes": list(scopes)}, headers=alex
    ).json()
    return {"Authorization": f"Bearer {tok['token']}"}


def _admin(client, alex, handle="agent", kind="agent"):
    """An account with the admin role and an unlimited admin token."""
    acct = client.post(
        f"{API}/accounts",
        json={"handle": handle, "display_name": handle.title(), "kind": kind, "role": "admin"},
        headers=alex,
    ).json()
    return acct, _bearer(client, alex, acct["id"], ["admin"])


def _item(client, headers, todo, title="Buy milk"):
    r = client.post(f"{API}/items", json={"list_id": todo["id"], "title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _trashed(client, headers, todo, title="Old"):
    item = _item(client, headers, todo, title)
    assert client.post(f"{API}/items/{item['id']}/trash", headers=headers).status_code == 200
    return item


def test_default_applies_when_nothing_is_saved(client, alex):
    r = client.get(f"{API}/settings/agents", headers=alex)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default"] is True
    assert body["denied"] == ["delete", "archive_structure", "share", "tokens"]
    assert [a["key"] for a in body["actions"]] == list(agent_rules.ACTIONS)


def test_agent_on_an_admin_token_is_refused_a_denied_action(client, alex, todo):
    acct, agent = _admin(client, alex)
    item = _trashed(client, alex, todo)
    r = client.post(f"{API}/items/{item['id']}/purge", headers=agent)
    assert r.status_code == 403
    assert "agents may not delete things for good here (workspace rule)" in r.text
    r = client.patch(f"{API}/lists/{todo['id']}", json={"archived": True}, headers=agent)
    assert r.status_code == 403 and "archive sections or lists" in r.text
    r = client.post(
        f"{API}/accounts",
        json={"handle": "x1", "display_name": "X", "kind": "agent"},
        headers=agent,
    )
    assert r.status_code == 403 and "workspace rule" in r.text
    r = client.post(f"{API}/accounts/{acct['id']}/tokens", json={"scopes": ["read"]}, headers=agent)
    assert r.status_code == 403 and "workspace rule" in r.text
    # Not denied by default: these work.
    mine = _item(client, agent, todo, "Agent's task")
    assert client.post(f"{API}/items/{mine['id']}/trash", headers=agent).status_code == 200
    r = client.patch(f"{API}/lists/{todo['id']}", json={"name": "Todo 2"}, headers=agent)
    assert r.status_code == 200, r.text


def test_undoing_your_own_new_item_is_not_a_delete_for_good(client, alex, todo):
    _, agent = _admin(client, alex)
    mine = _item(client, agent, todo, "Filed twice")
    assert client.delete(f"{API}/items/{mine['id']}", headers=agent).status_code == 204


def test_an_agent_may_still_revoke_its_own_token(client, alex):
    acct, agent = _admin(client, alex)
    toks = client.get(f"{API}/accounts/{acct['id']}/tokens", headers=agent).json()
    r = client.delete(f"{API}/tokens/{toks[0]['id']}", headers=agent)
    assert r.status_code in (200, 204), r.text


def test_people_are_never_refused(client, alex, todo):
    _, member = _admin(client, alex, "member", kind="person")
    item = _trashed(client, alex, todo)
    assert client.post(f"{API}/items/{item['id']}/purge", headers=member).status_code == 204
    item = _trashed(client, alex, todo, "Older")
    assert client.post(f"{API}/items/{item['id']}/purge", headers=alex).status_code == 204
    r = client.patch(f"{API}/lists/{todo['id']}", json={"archived": True}, headers=member)
    assert r.status_code == 200, r.text


def test_changing_the_list_is_an_event_with_an_actor_and_takes_effect(client, alex, todo):
    _, agent = _admin(client, alex)
    me = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    r = client.put(f"{API}/settings/agents", json={"denied": ["export", "trash"]}, headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["denied"] == ["trash", "export"]
    assert r.json()["default"] is False
    with SessionLocal() as s:
        ev = s.scalars(select(Event).where(Event.type == "settings.changed")).one()
        assert ev.actor_id == me
        assert ev.field == "agents.denied"
        assert ev.before_json == ["delete", "archive_structure", "share", "tokens"]
        assert ev.after_json == ["trash", "export"]
    item = _item(client, alex, todo)
    r = client.post(f"{API}/items/{item['id']}/trash", headers=agent)
    assert r.status_code == 403 and "move tasks to the trash" in r.text
    r = client.get(f"{API}/export", headers=agent)
    assert r.status_code == 403 and "agents may not export here" in r.text
    assert client.get(f"{API}/export", headers=alex).status_code == 200
    # Purging is no longer denied.
    item = _trashed(client, alex, todo)
    assert client.post(f"{API}/items/{item['id']}/purge", headers=agent).status_code == 204
    # Saving the same list again records nothing.
    client.put(f"{API}/settings/agents", json={"denied": ["trash", "export"]}, headers=alex)
    with SessionLocal() as s:
        assert len(s.scalars(select(Event).where(Event.type == "settings.changed")).all()) == 1


def test_only_people_who_manage_may_change_it(client, alex, make_account):
    _, agent = _admin(client, alex)
    r = client.put(f"{API}/settings/agents", json={"denied": []}, headers=agent)
    assert r.status_code == 403 and "agents may not change the agent rules" in r.text
    _, member = make_account("member2", ("write",), "person")
    assert client.get(f"{API}/settings/agents", headers=member).status_code == 403
    r = client.put(f"{API}/settings/agents", json={"denied": []}, headers=member)
    assert r.status_code == 403
    r = client.put(f"{API}/settings/agents", json={"denied": ["nope"]}, headers=alex)
    assert r.status_code == 422


def test_batch_goes_through_the_same_check(client, alex, todo):
    _, agent = _admin(client, alex)
    r = client.post(
        f"{API}/batch",
        json={
            "ops": [
                {
                    "tool": "list",
                    "action": "update",
                    "params": {"id": todo["id"], "archived": True},
                }
            ]
        },
        headers=agent,
    )
    assert r.status_code == 403 and "workspace rule" in r.text, r.text


async def test_mcp_refuses_and_reads_the_rules(client, alex, todo):
    _, agent = _admin(client, alex)
    item = _trashed(client, alex, todo)
    async with http_mcp(agent) as mcp:
        assert "workspace rule" in err(await call(mcp, "slate_item", "purge", id=item["id"]))
        assert "agents may not change the agent rules" in err(
            await call(mcp, "slate_account", "set_agent_rules", denied=[])
        )
    async with http_mcp(alex) as mcp:
        assert ok(await call(mcp, "slate_account", "agent_rules"))["default"] is True
        saved = ok(await call(mcp, "slate_account", "set_agent_rules", denied=["automations"]))
        assert saved["denied"] == ["automations"]


def test_slate_own_work_is_never_refused(client, alex, todo, db):
    """The scheduler and automations act as the `slate` agent; the rule is for agents people
    run."""
    from slate.service import trash as trash_svc
    from slate.service.due import system_actor

    item = _trashed(client, alex, todo)
    trash_svc.purge_item(db, system_actor(db), item["id"])
