"""The item cache behind `GET /items` must never answer differently from a fresh build.

Each test warms the cache, makes one kind of change through the API, then compares the cached
answer with the answer after the cache is emptied.
"""

from __future__ import annotations

import pytest

from conftest import API
from slate.service import item_cache

QUERIES = [{}, {"include_archived": "true"}]


def _same_as_fresh(client, headers):
    cached = [client.get(f"{API}/items", params=q, headers=headers).json() for q in QUERIES]
    item_cache.cache.clear()
    fresh = [client.get(f"{API}/items", params=q, headers=headers).json() for q in QUERIES]
    assert cached == fresh
    return cached[0]


@pytest.fixture
def board(client, alex, todo):
    def new(title, **extra):
        r = client.post(
            f"{API}/items", json={"list_id": todo["id"], "title": title, **extra}, headers=alex
        )
        assert r.status_code == 201, r.text
        return r.json()

    a, b, c = new("a"), new("b"), new("c")
    child = new("child", parent_id=a["id"])
    client.post(
        f"{API}/items/{b['id']}/links",
        json={"kind": "blocked_by", "item_id": c["id"]},
        headers=alex,
    )
    _same_as_fresh(client, alex)  # warm
    return {"a": a, "b": b, "c": c, "child": child, "list": todo}


def _warm(client, alex):
    for q in QUERIES:
        client.get(f"{API}/items", params=q, headers=alex)


def test_cache_serves_hits(client, alex, board):
    _warm(client, alex)
    before = item_cache.cache.stats()["hits"]
    _warm(client, alex)
    assert item_cache.cache.stats()["hits"] > before


CHANGES = {
    "title": lambda c, h, b: c.patch(
        f"{API}/items/{b['a']['id']}", json={"title": "A2"}, headers=h
    ),
    "blocker done": lambda c, h, b: c.patch(
        f"{API}/items/{b['c']['id']}", json={"status": "Done"}, headers=h
    ),
    "child archived": lambda c, h, b: c.post(f"{API}/items/{b['child']['id']}/archive", headers=h),
    "child trashed": lambda c, h, b: c.post(f"{API}/items/{b['child']['id']}/trash", headers=h),
    "child added": lambda c, h, b: c.post(
        f"{API}/items",
        json={"list_id": b["list"]["id"], "title": "kid", "parent_id": b["b"]["id"]},
        headers=h,
    ),
    "link": lambda c, h, b: c.post(
        f"{API}/items/{b['a']['id']}/links",
        json={"kind": "blocks", "item_id": b["c"]["id"]},
        headers=h,
    ),
    "unlink": lambda c, h, b: c.delete(
        f"{API}/items/{b['b']['id']}/links",
        params={"kind": "blocked_by", "other": b["c"]["id"]},
        headers=h,
    ),
    "comment": lambda c, h, b: c.post(
        f"{API}/items/{b['a']['id']}/comments", json={"body_md": "hi"}, headers=h
    ),
    "checklist": lambda c, h, b: c.post(
        f"{API}/items/{b['a']['id']}/checklists", json={"items": ["x", "y"]}, headers=h
    ),
    "tags": lambda c, h, b: c.patch(
        f"{API}/items/{b['a']['id']}", json={"tags": ["home", "work"]}, headers=h
    ),
    "tag renamed": lambda c, h, b: (
        c.patch(f"{API}/items/{b['a']['id']}", json={"tags": ["home"]}, headers=h),
        _warm(c, h),
        c.patch(f"{API}/tags/home", json={"name": "house"}, headers=h),
    )[-1],
    "statuses renamed": lambda c, h, b: c.put(
        f"{API}/lists/{b['list']['id']}/statuses",
        json={
            "statuses": [
                {"name": "Backlog", "kind": "open"},
                {"name": "Doing", "kind": "active"},
                {"name": "Shipped", "kind": "done"},
            ],
            "fallback": "Backlog",
        },
        headers=h,
    ),  # fmt: skip
    "assignee renamed": lambda c, h, b: (
        c.patch(f"{API}/items/{b['a']['id']}", json={"assignees": ["alex"]}, headers=h),
        _warm(c, h),
        c.patch(
            f"{API}/accounts/{c.get(f'{API}/me', headers=h).json()['account']['id']}",
            json={"display_name": "Alex Renamed"},
            headers=h,
        ),
    )[-1],  # fmt: skip
    "report": lambda c, h, b: c.post(
        f"{API}/items/{b['a']['id']}/report", json={"state": "working", "note": "on it"}, headers=h
    ),
    "list archived": lambda c, h, b: c.patch(
        f"{API}/lists/{b['list']['id']}", json={"archived": True}, headers=h
    ),
    "deleted": lambda c, h, b: c.delete(f"{API}/items/{b['b']['id']}", headers=h),
}


@pytest.mark.parametrize("change", CHANGES)
def test_cached_items_match_a_fresh_build_after(change, client, alex, board):
    r = CHANGES[change](client, alex, board)
    assert r.status_code < 400, r.text
    _same_as_fresh(client, alex)


def test_ids_filter_returns_those_items_in_order(client, alex, board):
    ids = [board["c"]["id"], board["a"]["id"], "nope"]
    got = client.get(f"{API}/items", params={"ids": ",".join(ids)}, headers=alex).json()
    assert [i["id"] for i in got] == [board["a"]["id"], board["c"]["id"]]  # position order


def test_a_write_from_another_process_drops_the_cache(client, alex, board, db):
    """No in-process event: the cache sees the database's newest event moved and starts over."""
    from sqlalchemy import text

    from slate.ids import new_event_id

    _warm(client, alex)
    actor = client.get(f"{API}/me", headers=alex).json()["account"]["id"]
    # One shared in-memory connection in tests: no request may run between these and commit.
    db.execute(text("UPDATE items SET title = 'outside' WHERE id = :id"), {"id": board["a"]["id"]})
    db.execute(
        text(
            "INSERT INTO events (id, item_id, subject_id, actor_id, source, type, at) "
            "VALUES (:id, :item, :item, :actor, 'system', 'item.updated', "
            "'2026-10-24T00:00:00.000000Z')"
        ),
        {"id": new_event_id(), "item": board["a"]["id"], "actor": actor},
    )
    db.commit()
    titles = {i["id"]: i["title"] for i in client.get(f"{API}/items", headers=alex).json()}
    assert titles[board["a"]["id"]] == "outside"


def test_fields_reads_are_cut_from_the_cache(client, alex, board):
    whole = client.get(f"{API}/items", headers=alex).json()
    hits = item_cache.cache.hits
    trimmed = client.get(f"{API}/items?fields=title,status,blocked", headers=alex).json()
    assert item_cache.cache.hits > hits
    keep = {"id", "title", "status", "blocked"}
    assert trimmed == [{k: v for k, v in i.items() if k in keep} for i in whole]
    assert list(trimmed[0]) == [k for k in whole[0] if k in keep]  # field order kept


def test_checkin_queue_comes_from_the_cache(client, alex, board):
    for item in client.get(f"{API}/items", headers=alex).json():
        client.patch(f"{API}/items/{item['id']}", json={"assignees": ["alex"]}, headers=alex)
    full = client.get(f"{API}/checkin", headers=alex).json()
    assert len(full["queue"]) == 4
    item_cache.cache.clear()
    assert client.get(f"{API}/checkin", headers=alex).json()["queue"] == full["queue"]
    short = client.get(f"{API}/checkin?fields=title,due_at", headers=alex).json()
    assert short["queue"] == [
        {"id": i["id"], "title": i["title"], "due_at": i["due_at"]} for i in full["queue"]
    ]
