"""Blocked state: derived from open blockers, with item.blocked / item.unblocked on each turn."""

from __future__ import annotations

from sqlalchemy import select

from conftest import API
from slate.models import Event, Item, Source
from slate.service import automations, items, links, lists
from slate.service.core import Actor


def _item(client, h, list_id, title="Thing", **fields):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **fields}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _wait(client, h, item_id, on_id):
    r = client.post(
        f"{API}/items/{item_id}/links", json={"kind": "blocked_by", "item_id": on_id}, headers=h
    )
    assert r.status_code in (200, 201), r.text


def _turns(client, h, item_id):
    """The item's item.blocked / item.unblocked events, oldest first."""
    return [
        e
        for e in client.get(f"{API}/items/{item_id}/events", headers=h).json()
        if e["type"] in ("item.blocked", "item.unblocked")
    ]


def _get(client, h, item_id):
    return client.get(f"{API}/items/{item_id}", headers=h).json()


def test_finishing_the_blocker_unblocks_and_reopening_blocks_again(client, alex, todo):
    a = _item(client, alex, todo["id"], "Pour foundation")
    b = _item(client, alex, todo["id"], "Frame walls")
    _wait(client, alex, b["id"], a["id"])
    turns = _turns(client, alex, b["id"])
    assert [t["type"] for t in turns] == ["item.blocked"]
    assert turns[0]["after"] == {"blocked_by": [a["id"]]}
    assert _get(client, alex, b["id"])["blocked"] is True

    client.patch(f"{API}/items/{a['id']}", json={"status": "Done"}, headers=alex)
    turns = _turns(client, alex, b["id"])
    assert [t["type"] for t in turns] == ["item.blocked", "item.unblocked"]
    assert turns[1]["after"] == {"cleared_by": [a["id"]], "open": True}
    assert turns[1]["before"]["blocked_since"]
    assert _get(client, alex, b["id"])["blocked"] is False

    client.patch(f"{API}/items/{a['id']}", json={"status": "To do"}, headers=alex)
    assert [t["type"] for t in _turns(client, alex, b["id"])][-1] == "item.blocked"
    # Editing the blocker in other ways is no turn.
    client.patch(f"{API}/items/{a['id']}", json={"title": "Pour it"}, headers=alex)
    assert len(_turns(client, alex, b["id"])) == 3
    # Nothing is recorded on the blocker itself.
    assert _turns(client, alex, a["id"]) == []


def test_unblocked_only_when_the_last_blocker_closes(client, alex, todo):
    a, c = _item(client, alex, todo["id"], "A"), _item(client, alex, todo["id"], "C")
    b = _item(client, alex, todo["id"], "B")
    _wait(client, alex, b["id"], a["id"])
    _wait(client, alex, b["id"], c["id"])
    assert [t["type"] for t in _turns(client, alex, b["id"])] == ["item.blocked"]
    client.patch(f"{API}/items/{a['id']}", json={"status": "Done"}, headers=alex)
    assert [t["type"] for t in _turns(client, alex, b["id"])] == ["item.blocked"]
    assert _get(client, alex, b["id"])["blocked"] is True
    client.post(f"{API}/items/{c['id']}/archive", headers=alex)
    turns = _turns(client, alex, b["id"])
    assert [t["type"] for t in turns] == ["item.blocked", "item.unblocked"]
    assert turns[-1]["after"]["cleared_by"] == [c["id"]]


def test_trash_restore_and_unlink(client, alex, todo):
    a = _item(client, alex, todo["id"], "A")
    b = _item(client, alex, todo["id"], "B")
    _wait(client, alex, b["id"], a["id"])
    assert client.post(f"{API}/items/{a['id']}/trash", headers=alex).status_code == 200
    assert _turns(client, alex, b["id"])[-1]["type"] == "item.unblocked"
    r = client.post(f"{API}/items/{a['id']}/restore", headers=alex)
    assert r.status_code == 200, r.text
    assert _turns(client, alex, b["id"])[-1]["type"] == "item.blocked"
    client.delete(
        f"{API}/items/{b['id']}/links",
        params={"kind": "blocked_by", "other": a["id"]},
        headers=alex,
    )
    last = _turns(client, alex, b["id"])[-1]
    assert last["type"] == "item.unblocked" and last["after"]["cleared_by"] == [a["id"]]


def test_a_status_turning_done_unblocks(client, alex, todo):
    a = _item(client, alex, todo["id"], "A", status="In progress")
    b = _item(client, alex, todo["id"], "B")
    _wait(client, alex, b["id"], a["id"])
    statuses = client.get(f"{API}/lists/{todo['id']}", headers=alex).json()["statuses"]
    specs = [
        {
            "id": s["id"],
            "name": s["name"],
            "kind": "done" if s["name"] == "In progress" else s["kind"],
        }
        for s in statuses
    ]
    r = client.put(f"{API}/lists/{todo['id']}/statuses", json={"statuses": specs}, headers=alex)
    assert r.status_code == 200, r.text
    assert _turns(client, alex, b["id"])[-1]["type"] == "item.unblocked"


def test_assignee_hears_unblocked_and_closed_items_stay_quiet(client, alex, todo, make_account):
    agent, ch = make_account("agent")
    a = _item(client, alex, todo["id"], "Design")
    b = _item(client, alex, todo["id"], "Build", assignees=["agent"])
    c = _item(client, alex, todo["id"], "Old", assignees=["agent"])
    _wait(client, alex, b["id"], a["id"])
    _wait(client, alex, c["id"], a["id"])
    client.patch(f"{API}/items/{c['id']}", json={"status": "Done"}, headers=alex)
    client.patch(f"{API}/items/{a['id']}", json={"status": "Done"}, headers=alex)
    entries = client.get(f"{API}/inbox", params={"reason": "unblocked"}, headers=ch).json()
    assert [e["event"]["item_id"] for e in entries] == [b["id"]]
    quiet = _turns(client, alex, c["id"])[-1]
    assert quiet["type"] == "item.unblocked" and quiet["after"]["open"] is False
    # Alex's own change is no news to Alex, even on Alex's task.
    d = _item(client, alex, todo["id"], "Mine", assignees=["alex"])
    _wait(client, alex, d["id"], b["id"])
    client.patch(f"{API}/items/{b['id']}", json={"status": "Done"}, headers=alex)
    assert client.get(f"{API}/inbox", params={"reason": "unblocked"}, headers=alex).json() == []
    # The agent finishing it is.
    client.patch(f"{API}/items/{b['id']}", json={"status": "To do"}, headers=alex)
    client.patch(f"{API}/items/{b['id']}", json={"status": "Done"}, headers=ch)
    entries = client.get(f"{API}/inbox", params={"reason": "unblocked"}, headers=alex).json()
    assert [e["event"]["item_id"] for e in entries] == [d["id"]]


def test_a_list_limited_agent_can_finish_a_blocker_of_a_task_it_cannot_edit(
    client, alex, todo, make_account
):
    bot, _ = make_account("bot")
    other = client.post(
        f"{API}/lists", json={"section_id": todo["section_id"], "name": "Elsewhere"}, headers=alex
    ).json()
    a = _item(client, alex, todo["id"], "A")
    b = _item(client, alex, other["id"], "B")
    _wait(client, alex, b["id"], a["id"])
    tok = client.post(
        f"{API}/accounts/{bot['id']}/tokens",
        json={"scopes": ["write"], "limit": {"list_ids": [todo["id"]]}},
        headers=alex,
    )
    assert tok.status_code == 201, tok.text
    bh = {"Authorization": f"Bearer {tok.json()['token']}"}
    r = client.patch(f"{API}/items/{a['id']}", json={"status": "Done"}, headers=bh)
    assert r.status_code == 200, r.text
    last = _turns(client, alex, b["id"])[-1]
    assert last["type"] == "item.unblocked" and last["actor_id"] == bot["id"]


def test_task_unblocked_automation(client, alex, todo, db):
    me = client.get(f"{API}/me", headers=alex).json()
    actor = Actor(account_id=me["account"]["id"], source=Source.api)
    automations.start(db)
    r = client.post(
        f"{API}/automations",
        json={
            "name": "Ready",
            "list_id": todo["id"],
            "trigger": {"type": "task_unblocked"},
            "actions": [{"type": "set_status", "status": "In progress"}],
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    a = items.create_item(db, actor, list_id=todo["id"], title="A")
    b = items.create_item(db, actor, list_id=todo["id"], title="B")
    links.add_link(db, actor, b.id, kind="blocked_by", other_id=a.id)
    assert automations.process(db) == 0
    items.update_item(db, actor, a.id, {"status": "Done"})
    assert automations.process(db) == 1
    db.expire_all()
    assert items.views(db, [db.get(Item, b.id)])[0]["status"]["name"] == "In progress"
    assert "task_unblocked" in {t["type"] for t in automations.catalog()["triggers"]}


def test_changes_that_touch_no_dependency_record_no_turn(db, client, alex, todo):
    me = client.get(f"{API}/me", headers=alex).json()
    actor = Actor(account_id=me["account"]["id"], source=Source.api)
    lst = lists.get_list(db, todo["id"])
    a = items.create_item(db, actor, list_id=lst.id, title="A")
    items.update_item(db, actor, a.id, {"status": "Done"})
    n = db.scalar(
        select(Event.id).where(Event.type.in_(["item.blocked", "item.unblocked"])).limit(1)
    )
    assert n is None
