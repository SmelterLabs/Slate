"""Estimates, dependencies, tag merge/delete and status templates over REST."""

from __future__ import annotations

from conftest import API
from conftest import item_events as _events


def _item(client, h, list_id, title="Thing", **fields):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": title, **fields}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _other_list(client, h, todo, name="Later"):
    r = client.post(
        f"{API}/lists", json={"section_id": todo["section_id"], "name": name}, headers=h
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- estimates -----------------------------------------------------------------------------


def test_estimate_minutes(client, alex, todo):
    item = _item(client, alex, todo["id"], estimate_minutes=90)
    assert item["estimate_minutes"] == 90
    r = client.patch(f"{API}/items/{item['id']}", json={"estimate_minutes": None}, headers=alex)
    assert r.json()["estimate_minutes"] is None
    r = client.patch(f"{API}/items/{item['id']}", json={"estimate_minutes": -1}, headers=alex)
    assert r.status_code == 422


# --- dependencies ---------------------------------------------------------------------------


def test_blocking_links_both_sides_and_blocked_filter(client, alex, todo):
    a = _item(client, alex, todo["id"], "Pour foundation")
    b = _item(client, alex, todo["id"], "Frame walls")
    r = client.post(
        f"{API}/items/{b['id']}/links",
        json={"kind": "blocked_by", "item_id": a["id"]},
        headers=alex,
    )
    assert r.json() == {
        "blocked_by": [a["id"]],
        "blocking": [],
        "related": [],
        "duplicate_of": [],
        "duplicated_by": [],
        "blocked": True,
    }
    got_a = client.get(f"{API}/items/{a['id']}", headers=alex).json()
    assert got_a["blocking"] == [b["id"]] and not got_a["blocked"]
    blocked = client.get(f"{API}/items?blocked=true", headers=alex).json()
    assert [i["id"] for i in blocked] == [b["id"]]
    # Finishing the blocker unblocks.
    client.patch(f"{API}/items/{a['id']}", json={"status": "Done"}, headers=alex)
    assert client.get(f"{API}/items?blocked=true", headers=alex).json() == []
    assert {e["type"] for e in _events(client, alex, a["id"])} >= {"item.linked"}
    r = client.delete(
        f"{API}/items/{a['id']}/links", params={"kind": "blocks", "other": b["id"]}, headers=alex
    )
    assert r.json()["blocking"] == []
    assert _events(client, alex, b["id"])[-1]["type"] == "item.unlinked"


def test_dependency_loops_and_self_links_are_refused(client, alex, todo):
    a, b, c = (_item(client, alex, todo["id"], t) for t in "abc")
    for x, y in [(a, b), (b, c)]:
        r = client.post(
            f"{API}/items/{x['id']}/links",
            json={"kind": "blocks", "item_id": y["id"]},
            headers=alex,
        )
        assert r.status_code == 200
    r = client.post(
        f"{API}/items/{c['id']}/links", json={"kind": "blocks", "item_id": a["id"]}, headers=alex
    )
    assert r.status_code == 422 and "loop" in r.json()["detail"]
    r = client.post(
        f"{API}/items/{a['id']}/links", json={"kind": "relates", "item_id": a["id"]}, headers=alex
    )
    assert r.status_code == 422


def test_relates_is_symmetric_and_idempotent(client, alex, todo):
    a, b = (_item(client, alex, todo["id"], t) for t in "ab")
    for _ in range(2):
        client.post(
            f"{API}/items/{b['id']}/links",
            json={"kind": "relates", "item_id": a["id"]},
            headers=alex,
        )
    assert client.get(f"{API}/items/{a['id']}/links", headers=alex).json()["related"] == [b["id"]]
    linked = [e for e in _events(client, alex, a["id"]) if e["type"] == "item.linked"]
    assert len(linked) == 1


# --- tags -----------------------------------------------------------------------------------


def test_tag_counts_merge_and_delete(client, alex, todo):
    one = _item(client, alex, todo["id"], tags=["ai project", "ai"])
    two = _item(client, alex, todo["id"], tags=["ai project"])
    rule = client.post(
        f"{API}/recurrences",
        json={
            "list_id": todo["id"],
            "title_template": "x",
            "rrule": "FREQ=DAILY",
            "tags": ["ai project"],
        },
        headers=alex,
    ).json()
    counts = {t["name"]: t["item_count"] for t in client.get(f"{API}/tags", headers=alex).json()}
    assert counts == {"ai": 1, "ai project": 2}

    r = client.post(f"{API}/tags/ai project/merge", json={"into": "ai"}, headers=alex)
    assert r.status_code == 200 and r.json()["name"] == "ai"
    for item in (one, two):
        assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["tags"] == ["ai"]
    assert client.get(f"{API}/recurrences/{rule['id']}", headers=alex).json()["tags"] == ["ai"]
    assert [t["name"] for t in client.get(f"{API}/tags", headers=alex).json()] == ["ai"]

    assert client.delete(f"{API}/tags/ai", headers=alex).status_code == 204
    assert client.get(f"{API}/items/{one['id']}", headers=alex).json()["tags"] == []
    assert client.get(f"{API}/tags", headers=alex).json() == []
    assert client.delete(f"{API}/tags/ai", headers=alex).status_code == 404


def test_tag_rename_follows_into_rules(client, alex, todo):
    client.post(f"{API}/tags", json={"name": "h3"}, headers=alex)
    rule = client.post(
        f"{API}/recurrences",
        json={"list_id": todo["id"], "title_template": "x", "rrule": "FREQ=DAILY", "tags": ["h3"]},
        headers=alex,
    ).json()
    client.patch(f"{API}/tags/h3", json={"name": "h4"}, headers=alex)
    assert client.get(f"{API}/recurrences/{rule['id']}", headers=alex).json()["tags"] == ["h4"]


# --- views over the new data ----------------------------------------------------------------


def test_views_accept_table_estimate_and_blocked(client, alex, todo):
    config = {
        "layout": "table",
        "sort": [{"field": "estimate", "dir": "desc"}],
        "filter": {
            "op": "and",
            "rules": [
                {"field": "estimate", "cmp": "not_empty", "value": None},
                {"field": "blocked", "cmp": "is", "value": False},
            ],
        },
        "card_fields": ["due", "estimate", "blocked"],
    }
    r = client.post(
        f"{API}/views",
        json={"name": "Table", "scope_kind": "list", "scope_id": todo["id"], "config": config},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    assert r.json()["config"]["layout"] == "table"
    assert r.json()["config"]["sort"] == [{"field": "estimate", "dir": "desc"}]
    bad = {**config, "layout": "gantt"}
    r = client.post(
        f"{API}/views",
        json={"name": "G", "scope_kind": "list", "scope_id": todo["id"], "config": bad},
        headers=alex,
    )
    assert r.status_code == 422
