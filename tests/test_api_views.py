"""Saved views and bulk item edits."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from slate.models import View

API = "/api/v1"

BOARD = {
    "layout": "board",
    "group_by": "due",
    "due_window_days": 6,
    "sort": [{"field": "priority", "dir": "desc"}],
    "filter": {"op": "and", "rules": [{"field": "tag", "cmp": "any", "value": ["home"]}]},
    "show_closed": False,
}


@pytest.fixture
def todo(client, alex):
    section = client.post(
        f"{API}/sections", json={"name": "Personal", "audience": "personal"}, headers=alex
    ).json()
    return client.post(
        f"{API}/lists", json={"section_id": section["id"], "name": "Todo"}, headers=alex
    ).json()


def test_bootstrap_seeds_the_landing_views(client, alex, db):
    views = client.get(f"{API}/views", headers=alex).json()
    names = [v["name"] for v in views]
    assert names[:2] == ["This week", "Agenda"]
    week = views[0]
    assert week["scope_kind"] == "everything" and week["scope_id"] is None
    assert week["config"]["group_by"] == "due" and week["config"]["due_window_days"] == 6
    assert db.get(View, "vwthisweek01") is not None


def test_seeding_is_idempotent(db, alex, client):
    from slate.service.accounts import Principal
    from slate.service.core import Actor
    from slate.service.views import seed_default_views

    actor = Actor(account_id=db.query(View).first().owner_id, source="api")
    assert seed_default_views(db, actor) == []
    assert client.get(f"{API}/views", headers=alex).json().__len__() == 2
    del Principal


def test_create_list_and_update_a_view(client, alex, todo):
    made = client.post(
        f"{API}/views",
        json={"name": "Home board", "scope_kind": "list", "scope_id": todo["id"], "config": BOARD},
        headers=alex,
    )
    assert made.status_code == 201, made.text
    view = made.json()
    assert view["config"]["layout"] == "board" and view["owner_id"]

    renamed = client.patch(f"{API}/views/{view['id']}", json={"name": "Home"}, headers=alex)
    assert renamed.status_code == 200 and renamed.json()["name"] == "Home"

    scoped = client.get(f"{API}/views?scope=list:{todo['id']}", headers=alex).json()
    assert [v["id"] for v in scoped] == [view["id"]]

    archived = client.post(f"{API}/views/{view['id']}/archive", headers=alex)
    assert archived.json()["archived_at"] is not None
    assert client.get(f"{API}/views?scope=list:{todo['id']}", headers=alex).json() == []
    back = client.post(f"{API}/views/{view['id']}/unarchive", headers=alex)
    assert back.json()["archived_at"] is None


def test_bad_config_is_refused_in_plain_words(client, alex):
    def post(config, **extra):
        body = {"name": "x", "scope_kind": "everything", "config": config, **extra}
        return client.post(f"{API}/views", json=body, headers=alex)

    assert post({"group_by": "sideways"}).status_code == 422
    status_group = post({"group_by": "status"})
    assert status_group.status_code == 422 and "status_kind" in status_group.json()["detail"]
    assert post({"filter": {"op": "xor", "rules": []}}).status_code == 422
    assert post({"sort": [{"field": "colour", "dir": "asc"}]}).status_code == 422
    assert post({"mystery": True}).status_code == 422
    deep = {
        "op": "and",
        "rules": [
            {
                "op": "or",
                "rules": [
                    {
                        "op": "and",
                        "rules": [
                            {
                                "op": "or",
                                "rules": [{"field": "text", "cmp": "contains", "value": "x"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    assert post({"filter": deep}).status_code == 422
    assert post({"title_lines": 7}).status_code == 422
    assert post({"collapse_empty": "yes"}).status_code == 422
    assert post({"card_fields": ["due", "sparkles"]}).status_code == 422


def test_card_options_round_trip(client, alex):
    config = {
        **BOARD,
        "title_lines": 0,
        "collapse_empty": False,
        "card_fields": ["description", "comments", "due", "status", "start"],
    }
    body = {"name": "Cards", "scope_kind": "everything", "config": config}
    saved = client.post(f"{API}/views", json=body, headers=alex).json()["config"]
    assert saved["title_lines"] == 0 and saved["collapse_empty"] is False
    assert saved["card_fields"] == ["description", "comments", "due", "status", "start"]


def test_view_events_carry_the_actor(client, alex, db):
    from slate.models import Event

    resp = client.post(
        f"{API}/views",
        json={"name": "Mine", "scope_kind": "everything", "config": {"layout": "list"}},
        headers=alex,
    )
    assert resp.status_code == 201, resp.text
    made = resp.json()
    ev = db.query(Event).filter_by(type="view.created", subject_id=made["id"]).one()
    assert ev.actor_id == made["owner_id"] and ev.source.value == "api"


def _item(client, alex, todo, title, **extra):
    return client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": title, **extra}, headers=alex
    ).json()


def test_bulk_changes_tags_and_archive(client, alex, todo):
    a = _item(client, alex, todo, "A", tags=["home"])
    b = _item(client, alex, todo, "B")
    r = client.post(
        f"{API}/items/bulk",
        json={
            "ids": [a["id"], b["id"]],
            "changes": {"priority": "high", "add_tags": ["urgent-ish"], "remove_tags": ["home"]},
        },
        headers=alex,
    )
    assert r.status_code == 200, r.text
    by_id = {i["id"]: i for i in r.json()}
    assert by_id[a["id"]]["priority"] == "high" and by_id[a["id"]]["tags"] == ["urgent-ish"]
    assert by_id[b["id"]]["tags"] == ["urgent-ish"]

    archived = client.post(
        f"{API}/items/bulk",
        json={"ids": [a["id"]], "changes": {"archive": True}},
        headers=alex,
    )
    assert archived.json()[0]["archived_at"] is not None


def test_bulk_moves_between_lists_keeping_status_kind(client, alex, todo):
    section_id = client.get(f"{API}/lists/{todo['id']}", headers=alex).json()["section_id"]
    other = client.post(
        f"{API}/lists", json={"section_id": section_id, "name": "Later"}, headers=alex
    ).json()
    done = next(s for s in todo["statuses"] if s["kind"] == "done")
    item = _item(client, alex, todo, "Finished", status=done["id"])

    moved = client.post(
        f"{API}/items/bulk",
        json={"ids": [item["id"]], "changes": {"list_id": other["id"]}},
        headers=alex,
    ).json()[0]
    assert moved["list_id"] == other["id"]
    assert moved["status"]["kind"] == "done"
    assert moved["status"]["id"] != done["id"]


def test_bulk_is_all_or_nothing(client, alex, todo, db):
    from slate.models import Event

    item = _item(client, alex, todo, "Keep me")
    before = db.query(Event).count()
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [item["id"], "doesnotexist"], "changes": {"priority": "low"}},
        headers=alex,
    )
    assert r.status_code == 422 and "doesnotexist" in r.json()["detail"]
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["priority"] is None
    assert db.query(Event).count() == before

    cap = client.post(
        f"{API}/items/bulk",
        json={"ids": [f"id{n:010d}" for n in range(501)], "changes": {}},
        headers=alex,
    )
    assert cap.status_code == 422


def test_bulk_status_kind_across_lists_in_one_transaction(client, alex, todo, db):
    """`status_kind` picks each item's own list's first status of that kind, all or nothing."""
    from slate.models import Event

    section_id = client.get(f"{API}/lists/{todo['id']}", headers=alex).json()["section_id"]
    other = client.post(
        f"{API}/lists", json={"section_id": section_id, "name": "Inbox"}, headers=alex
    ).json()
    a = _item(client, alex, todo, "In Todo")
    b = _item(client, alex, other, "In Inbox")

    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [a["id"], b["id"]], "changes": {"status_kind": "done"}},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    by_id = {i["id"]: i for i in r.json()}
    for it, lst in ((a, todo), (b, other)):
        want = next(s for s in lst["statuses"] if s["kind"] == "done")
        assert by_id[it["id"]]["status"]["id"] == want["id"]

    # A list without that kind refuses the whole batch, naming the list; nothing is written.
    bare = client.post(
        f"{API}/lists",
        json={
            "section_id": section_id,
            "name": "Bare",
            "statuses": [{"name": "Open", "kind": "open"}, {"name": "Shipped", "kind": "done"}],
        },
        headers=alex,
    ).json()
    c = _item(client, alex, bare, "No active here")
    before = db.query(Event).count()
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [a["id"], c["id"]], "changes": {"status_kind": "active"}},
        headers=alex,
    )
    assert r.status_code == 422 and "Bare" in r.json()["detail"]
    assert db.query(Event).count() == before

    both = client.post(
        f"{API}/items/bulk",
        json={"ids": [a["id"]], "changes": {"status_kind": "done", "status": "x"}},
        headers=alex,
    )
    assert both.status_code == 422
    bad = client.post(
        f"{API}/items/bulk",
        json={"ids": [a["id"]], "changes": {"status_kind": "finished"}},
        headers=alex,
    )
    assert bad.status_code == 422


# --- layouts: board, list and table ----------------------------------------------------------


@pytest.mark.parametrize("layout", ["calendar", "timeline", "box"])
def test_a_removed_layout_is_refused_on_write(client, alex, todo, layout):
    old = {"name": "Old", "scope_kind": "list", "scope_id": todo["id"]}
    old["config"] = {"layout": layout}
    made = client.post(f"{API}/views", json=old, headers=alex)
    assert made.status_code == 422
    assert "board" in made.text and "table" in made.text and layout in made.text

    view = client.post(
        f"{API}/views",
        json={"name": "Kept", "scope_kind": "list", "scope_id": todo["id"], "config": BOARD},
        headers=alex,
    ).json()
    patched = client.patch(
        f"{API}/views/{view['id']}", json={"config": {**BOARD, "layout": layout}}, headers=alex
    )
    assert patched.status_code == 422
    kept = client.get(f"{API}/views/{view['id']}", headers=alex).json()
    assert kept["config"]["layout"] == "board"


def test_a_stored_removed_layout_reads_as_list(client, alex, todo, db):
    view = client.post(
        f"{API}/views",
        json={"name": "Plan", "scope_kind": "list", "scope_id": todo["id"], "config": BOARD},
        headers=alex,
    ).json()
    # A view saved while the timeline layout existed still holds it, with its settings.
    stored = {
        **db.get(View, view["id"]).config_json,
        "layout": "timeline",
        "calendar_span": "week",
        "timeline_zoom": "month",
    }
    db.execute(
        text("UPDATE views SET config_json = :c WHERE id = :id"),
        {"c": json.dumps(stored), "id": view["id"]},
    )
    db.commit()
    db.expire_all()

    for got in (
        client.get(f"{API}/views/{view['id']}", headers=alex).json(),
        next(v for v in client.get(f"{API}/views", headers=alex).json() if v["id"] == view["id"]),
    ):
        assert got["config"]["layout"] == "list"
        assert "calendar_span" not in got["config"] and "timeline_zoom" not in got["config"]
        assert got["config"]["group_by"] == "due"

    # What the read returns saves back as it is.
    config = client.get(f"{API}/views/{view['id']}", headers=alex).json()["config"]
    saved = client.patch(f"{API}/views/{view['id']}", json={"config": config}, headers=alex)
    assert saved.status_code == 200, saved.text
    assert saved.json()["config"]["layout"] == "list"


def test_old_layout_settings_are_accepted_and_dropped(client, alex, todo):
    # A board page opened before an upgrade still sends these two keys with every save.
    made = client.post(
        f"{API}/views",
        json={
            "name": "Old tab",
            "scope_kind": "list",
            "scope_id": todo["id"],
            "config": {**BOARD, "calendar_span": "week", "timeline_zoom": "day"},
        },
        headers=alex,
    )
    assert made.status_code == 201, made.text
    assert "calendar_span" not in made.json()["config"]
    assert "timeline_zoom" not in made.json()["config"]


def test_the_table_layout_is_kept(client, alex, todo):
    made = client.post(
        f"{API}/views",
        json={
            "name": "Grid",
            "scope_kind": "list",
            "scope_id": todo["id"],
            "config": {"layout": "table"},
        },
        headers=alex,
    )
    assert made.status_code == 201, made.text
    got = client.get(f"{API}/views/{made.json()['id']}", headers=alex).json()
    assert got["config"]["layout"] == "table"
