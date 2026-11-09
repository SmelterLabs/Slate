"""Favorites: per-account pinned lists, sections, views and items (the board's favorites bar)."""

from __future__ import annotations

from sqlalchemy import select

from slate.models import Event

API = "/api/v1"


def pin(client, headers, kind, target_id, **extra):
    return client.post(
        f"{API}/favorites", json={"kind": kind, "target_id": target_id, **extra}, headers=headers
    )


def test_pin_list_section_view_and_item_in_order(client, alex, todo):
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "Renew passport"}, headers=alex
    ).json()
    assert pin(client, alex, "list", todo["id"]).status_code == 201
    assert pin(client, alex, "section", todo["section_id"]).status_code == 201
    assert pin(client, alex, "view", "vwagenda0001").status_code == 201
    assert pin(client, alex, "item", item["id"]).status_code == 201

    favs = client.get(f"{API}/favorites", headers=alex).json()
    assert [(f["kind"], f["name"]) for f in favs] == [
        ("list", "Todo"),
        ("section", "Personal"),
        ("view", "Agenda"),
        ("item", "Renew passport"),
    ]
    assert [f["position"] for f in favs] == [0, 1, 2, 3]
    assert favs[0]["scope_kind"] == "list" and favs[0]["scope_id"] == todo["id"]
    assert favs[2]["scope_kind"] == "everything" and favs[2]["scope_id"] is None
    assert favs[3]["scope_kind"] == "list" and favs[3]["scope_id"] == todo["id"]
    assert all(f["available"] for f in favs)


def test_pinning_twice_returns_the_same_favorite(client, alex, todo, db):
    first = pin(client, alex, "list", todo["id"]).json()
    before = db.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))
    again = pin(client, alex, "list", todo["id"]).json()
    assert again["id"] == first["id"]
    assert db.scalar(select(Event.id).order_by(Event.id.desc()).limit(1)) == before
    assert len(client.get(f"{API}/favorites", headers=alex).json()) == 1


def test_unknown_target_and_kind_are_refused(client, alex):
    assert pin(client, alex, "list", "nope").status_code == 404
    assert pin(client, alex, "tag", "x").status_code == 422


def test_favorites_are_per_account(client, alex, todo, make_account):
    _, agent = make_account("agent", ("write",))
    pin(client, alex, "list", todo["id"])
    assert client.get(f"{API}/favorites", headers=agent).json() == []
    mine = pin(client, agent, "section", todo["section_id"]).json()
    theirs = client.get(f"{API}/favorites", headers=alex).json()[0]
    assert client.delete(f"{API}/favorites/{theirs['id']}", headers=agent).status_code == 404
    assert client.delete(f"{API}/favorites/{mine['id']}", headers=agent).status_code == 204
    assert client.get(f"{API}/favorites", headers=agent).json() == []
    assert len(client.get(f"{API}/favorites", headers=alex).json()) == 1


def test_reorder_and_insert_at_a_position(client, alex, todo):
    a = pin(client, alex, "list", todo["id"]).json()
    b = pin(client, alex, "section", todo["section_id"]).json()
    c = pin(client, alex, "view", "vwthisweek01", position=0).json()
    ids = [f["id"] for f in client.get(f"{API}/favorites", headers=alex).json()]
    assert ids == [c["id"], a["id"], b["id"]]

    r = client.put(
        f"{API}/favorites/order", json={"ids": [b["id"], c["id"], a["id"]]}, headers=alex
    )
    assert r.status_code == 200, r.text
    assert [f["id"] for f in r.json()] == [b["id"], c["id"], a["id"]]

    partial = client.put(f"{API}/favorites/order", json={"ids": [b["id"]]}, headers=alex)
    assert partial.status_code == 422


def test_archived_target_keeps_its_favorite_but_is_unavailable(client, alex, todo):
    pin(client, alex, "list", todo["id"])
    client.patch(f"{API}/lists/{todo['id']}", json={"archived": True}, headers=alex)
    [fav] = client.get(f"{API}/favorites", headers=alex).json()
    assert fav["available"] is False and fav["name"] == "Todo"


def test_every_favorite_change_is_an_event_with_the_actor(client, alex, todo, db):
    me = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    a = pin(client, alex, "list", todo["id"]).json()
    b = pin(client, alex, "section", todo["section_id"]).json()
    client.put(f"{API}/favorites/order", json={"ids": [b["id"], a["id"]]}, headers=alex)
    client.delete(f"{API}/favorites/{a['id']}", headers=alex)
    events = list(db.scalars(select(Event).where(Event.type.like("favorite.%")).order_by(Event.id)))
    assert [e.type for e in events] == [
        "favorite.added",
        "favorite.added",
        "favorite.moved",
        "favorite.moved",
        "favorite.removed",
    ]
    assert {e.actor_id for e in events} == {me}
    assert events[0].subject_id == a["id"] and events[0].after_json["target_id"] == todo["id"]


def test_read_token_can_list_but_not_pin(client, alex, todo, make_account):
    _, reader = make_account("watcher", ("read",))
    assert client.get(f"{API}/favorites", headers=reader).status_code == 200
    assert pin(client, reader, "list", todo["id"]).status_code == 403
