"""Revert from the log: `POST /events/{id}/revert` issues the inverse of an item change as the
caller (a new event, `session_ref` `revert:<event id>`), refuses when the field moved on since
(409) and for anything that is not a field change (422)."""

from __future__ import annotations

from sqlalchemy import select

from conftest import API
from slate.models import Event
from test_mcp import call, err, http_mcp, ok
from test_permissions import share


def _item(client, h, list_id, title="Thing", **kw):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _patch(client, h, item_id, **changes):
    r = client.patch(f"{API}/items/{item_id}", json=changes, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _events(client, h, item_id, type_=None, field=None):
    rows = client.get(f"{API}/items/{item_id}/events", headers=h).json()
    return [
        e
        for e in rows
        if (type_ is None or e["type"] == type_) and (field is None or e["field"] == field)
    ]


def _last(client, h, item_id, type_=None, field=None):
    return _events(client, h, item_id, type_, field)[-1]


def _revert(client, h, event_id):
    return client.post(f"{API}/events/{event_id}/revert", headers=h)


def test_reverting_a_field_change_writes_the_inverse_as_the_caller(
    client, alex, todo, make_account, db
):
    _, agent = make_account("agent")
    item = _item(client, alex, todo["id"], "Draft", priority="low", due_at="2030-01-09")
    _patch(client, agent, item["id"], title="Draft (agent edit)", priority="urgent")
    retitled = _last(client, alex, item["id"], "item.updated", "title")
    assert retitled["before"] == "Draft" and retitled["after"] == "Draft (agent edit)"

    r = _revert(client, alex, retitled["id"])
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Draft" and r.json()["priority"] == "urgent"  # one field only
    back = _last(client, alex, item["id"], "item.updated", "title")
    assert back["id"] != retitled["id"]
    assert back["before"] == "Draft (agent edit)" and back["after"] == "Draft"
    assert back["session_ref"] == f"revert:{retitled['id']}"
    me = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    assert back["actor_id"] == me and back["source"] == "api"
    db.expire_all()
    assert db.scalar(select(Event).where(Event.id == retitled["id"])) is not None  # append-only


def test_each_supported_type(client, alex, todo, make_account):
    _, agent = make_account("agent")
    make_account("agent2")
    section = client.get(f"{API}/sections", headers=alex).json()[0]
    other = client.post(
        f"{API}/lists", json={"section_id": section["id"], "name": "Later"}, headers=alex
    ).json()
    item = _item(
        client, alex, todo["id"], "Many", assignees=["agent"], tags=["a"], due_at="2030-01-09"
    )

    # status
    _patch(client, agent, item["id"], status="done")
    ev = _last(client, alex, item["id"], "item.status_changed")
    r = _revert(client, alex, ev["id"])
    assert r.status_code == 200 and r.json()["status"]["id"] == ev["before"], r.text
    assert r.json()["completed_at"] is None

    # assignees (handles in the event)
    _patch(client, agent, item["id"], assignees=["agent", "agent2"])
    ev = _last(client, alex, item["id"], "item.assigned")
    assert ev["after"] == ["agent", "agent2"]
    r = _revert(client, alex, ev["id"])
    assert r.status_code == 200 and [a["handle"] for a in r.json()["assignees"]] == ["agent"]

    # tags (item.updated, field tags)
    _patch(client, agent, item["id"], tags=["a", "b"])
    ev = _last(client, alex, item["id"], "item.updated", "tags")
    r = _revert(client, alex, ev["id"])
    assert r.status_code == 200 and r.json()["tags"] == ["a"]

    # a date-only due date keeps its date-only flag when put back
    _patch(client, agent, item["id"], due_at="2030-02-01T15:00:00Z")
    ev = _last(client, alex, item["id"], "item.updated", "due_at")
    r = _revert(client, alex, ev["id"])
    assert r.status_code == 200, r.text
    assert r.json()["due_at"] == item["due_at"] and r.json()["due_date_only"] is True

    # the description
    _patch(client, agent, item["id"], body_md="rewritten")
    ev = _last(client, alex, item["id"], "item.updated", "body_md")
    r = _revert(client, alex, ev["id"])
    assert r.status_code == 200 and r.json()["body_md"] == "", r.text

    # a move (item.moved)
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [item["id"]], "changes": {"list_id": other["id"]}},
        headers=agent,
    )
    assert r.status_code == 200, r.text
    ev = _last(client, alex, item["id"], "item.moved")
    assert ev["before"] == todo["id"] and ev["after"] == other["id"]
    r = _revert(client, alex, ev["id"])
    assert r.status_code == 200 and r.json()["list_id"] == todo["id"], r.text
    moved_back = _last(client, alex, item["id"], "item.moved")
    assert moved_back["session_ref"] == f"revert:{ev['id']}"


def test_changed_since_is_a_409_with_a_reason(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = _item(client, alex, todo["id"], "One")
    _patch(client, agent, item["id"], title="Two")
    first = _last(client, alex, item["id"], "item.updated", "title")
    _patch(client, alex, item["id"], title="Three")
    r = _revert(client, alex, first["id"])
    assert r.status_code == 409, r.text
    assert "changed since" in r.json()["detail"]
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["title"] == "Three"

    # Reverting the same change twice: the second is a changed-since too.
    latest = _last(client, alex, item["id"], "item.updated", "title")
    assert _revert(client, alex, latest["id"]).status_code == 200
    assert _revert(client, alex, latest["id"]).status_code == 409


def test_unsupported_events_are_422_and_unknown_404(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = _item(client, alex, todo["id"], "Thing")
    created = _last(client, alex, item["id"], "item.created")
    r = _revert(client, alex, created["id"])
    assert r.status_code == 422 and "cannot be reverted" in r.json()["detail"], r.text
    r = client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "hi"}, headers=agent)
    assert r.status_code == 201
    commented = _last(client, alex, item["id"], "item.commented")
    assert _revert(client, alex, commented["id"]).status_code == 422
    assert _revert(client, alex, "000000000000").status_code == 404


def test_a_guest_who_can_only_view_the_list_is_refused(client, alex, todo, make_account):
    # A person made the edit: a workspace agent's edits are invisible to a guest at all.
    _, agent = make_account("member", kind="person")
    item = _item(client, alex, todo["id"], "Seen")
    _patch(client, agent, item["id"], title="Seen (edited)")
    ev = _last(client, alex, item["id"], "item.updated", "title")
    guest = client.post(
        f"{API}/accounts",
        json={"handle": "guest", "display_name": "Guest", "kind": "person", "role": "guest"},
        headers=alex,
    ).json()
    tok = client.post(
        f"{API}/accounts/{guest['id']}/tokens", json={"scopes": ["write"]}, headers=alex
    ).json()
    gh = {"Authorization": f"Bearer {tok['token']}"}
    assert _revert(client, gh, ev["id"]).status_code == 404  # not even visible
    share(client, alex, guest["id"], "list", todo["id"], "view")
    assert _revert(client, gh, ev["id"]).status_code == 403
    share(client, alex, guest["id"], "list", todo["id"], "edit")
    assert _revert(client, gh, ev["id"]).status_code == 200
    assert _last(client, alex, item["id"], "item.updated", "title")["actor_id"] == guest["id"]


def test_revert_needs_a_write_token(client, alex, todo, make_account):
    _, reader = make_account("reader", scopes=("read",), kind="person")
    item = _item(client, alex, todo["id"], "Thing")
    _patch(client, alex, item["id"], title="Else")
    ev = _last(client, alex, item["id"], "item.updated", "title")
    assert _revert(client, reader, ev["id"]).status_code == 403


async def test_revert_through_mcp(client, alex, todo, make_account):
    _, agent = make_account("agent")
    item = _item(client, alex, todo["id"], "Draft")
    _patch(client, agent, item["id"], title="Changed")
    ev = _last(client, alex, item["id"], "item.updated", "title")
    async with http_mcp(alex) as mcp:
        out = ok(await call(mcp, "slate_item", "revert", event_id=ev["id"]))
        assert out["title"] == "Draft"
        msg = err(await call(mcp, "slate_item", "revert", event_id=ev["id"]))
        assert "changed since" in msg
