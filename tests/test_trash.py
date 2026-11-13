"""The trash: trash, restore, delete for good, auto-purge; and the archive query filters."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from slate.models import Event, Item, Source, utcnow
from slate.service import trash
from slate.service.core import Actor
from slate.settings import settings

API = "/api/v1"


def new_item(client, headers, todo, title="Thing", **extra):
    r = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": title, **extra}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def ids(client, headers, **params):
    r = client.get(f"{API}/items", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()]


def test_trash_hides_and_restore_brings_back(client, alex, todo):
    item = new_item(client, alex, todo)
    sub = new_item(client, alex, todo, title="Sub", parent_id=item["id"])
    r = client.post(f"{API}/items/{item['id']}/trash", headers=alex)
    assert r.status_code == 200, r.text
    shown = r.json()
    assert shown["trashed_at"] is not None and shown["archived_at"] == shown["trashed_at"]
    assert shown["purge_at"] is not None

    # Hidden everywhere, archived included; only the trash shows them.
    assert ids(client, alex, list_id=todo["id"]) == []
    assert ids(client, alex, list_id=todo["id"], include_archived="true") == []
    assert ids(client, alex, archived="true") == []
    assert set(ids(client, alex, trashed="true")) == {item["id"], sub["id"]}
    # Still readable by id (the task window of a trashed task).
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["trashed_at"]

    r = client.post(f"{API}/items/{item['id']}/restore", headers=alex)
    assert r.status_code == 200 and r.json()["trashed_at"] is None
    assert r.json()["archived_at"] is None
    assert set(ids(client, alex, list_id=todo["id"])) == {item["id"], sub["id"]}
    assert ids(client, alex, trashed="true") == []


def test_archived_before_trash_stays_archived(client, alex, todo):
    item = new_item(client, alex, todo)
    archived_at = client.post(f"{API}/items/{item['id']}/archive", headers=alex).json()[
        "archived_at"
    ]
    client.post(f"{API}/items/{item['id']}/trash", headers=alex)
    back = client.post(f"{API}/items/{item['id']}/restore", headers=alex).json()
    assert back["trashed_at"] is None and back["archived_at"] == archived_at


def test_subtask_trashed_alone_and_parent_rules(client, alex, todo):
    item = new_item(client, alex, todo)
    sub = new_item(client, alex, todo, title="Sub", parent_id=item["id"])
    client.post(f"{API}/items/{sub['id']}/trash", headers=alex)
    assert ids(client, alex, list_id=todo["id"]) == [item["id"]]
    # Trash the parent later; restoring the parent leaves the earlier-trashed subtask there.
    client.post(f"{API}/items/{item['id']}/trash", headers=alex)
    r = client.post(f"{API}/items/{sub['id']}/restore", headers=alex)
    assert r.status_code == 422 and "parent" in r.json()["detail"]
    assert client.post(f"{API}/items/{sub['id']}/purge", headers=alex).status_code == 422
    client.post(f"{API}/items/{item['id']}/restore", headers=alex)
    assert ids(client, alex, trashed="true") == [sub["id"]]


def test_trash_and_restore_carry_every_level(client, alex, todo):
    """Subtasks nest four deep: trashing the top takes grandchildren too, restoring brings
    them back."""
    top = new_item(client, alex, todo)
    child = new_item(client, alex, todo, title="Child", parent_id=top["id"])
    grand = new_item(client, alex, todo, title="Grand", parent_id=child["id"])
    client.post(f"{API}/items/{top['id']}/trash", headers=alex)
    assert set(ids(client, alex, trashed="true")) == {top["id"], child["id"], grand["id"]}
    client.post(f"{API}/items/{top['id']}/restore", headers=alex)
    assert ids(client, alex, trashed="true") == []
    assert set(ids(client, alex, list_id=todo["id"])) >= {top["id"], child["id"], grand["id"]}


def test_unarchive_and_bulk_refuse_trashed(client, alex, todo):
    item = new_item(client, alex, todo)
    client.post(f"{API}/items/{item['id']}/trash", headers=alex)
    assert client.post(f"{API}/items/{item['id']}/unarchive", headers=alex).status_code == 422
    body = {"ids": [item["id"]], "changes": {"archive": False}}
    r = client.post(f"{API}/items/bulk", json=body, headers=alex)
    assert r.status_code == 422, r.text


def test_purge_deletes_for_good_and_keeps_history(client, alex, todo, db):
    item = new_item(client, alex, todo)
    sub = new_item(client, alex, todo, title="Sub", parent_id=item["id"])
    client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "hi"}, headers=alex)
    # Only trashed items can be purged.
    assert client.post(f"{API}/items/{item['id']}/purge", headers=alex).status_code == 422
    client.post(f"{API}/items/{item['id']}/trash", headers=alex)
    assert client.post(f"{API}/items/{item['id']}/purge", headers=alex).status_code == 204
    assert client.get(f"{API}/items/{item['id']}", headers=alex).status_code == 404
    assert db.get(Item, sub["id"]) is None
    kinds = db.scalars(select(Event.type).where(Event.item_id == item["id"])).all()
    assert "item.trashed" in kinds and "item.purged" in kinds and "item.created" in kinds
    assert db.scalars(select(Event).where(Event.item_id == sub["id"], Event.type == "item.purged"))


def test_empty_trash_is_for_admins(client, alex, todo, make_account):
    _, agent = make_account("agent")
    a = new_item(client, alex, todo, title="A")
    b = new_item(client, alex, todo, title="B")
    new_item(client, alex, todo, title="B1", parent_id=b["id"])
    keep = new_item(client, alex, todo, title="Keep")
    for x in (a, b):
        client.post(f"{API}/items/{x['id']}/trash", headers=alex)
    assert client.post(f"{API}/trash/empty", headers=agent).status_code == 403
    r = client.post(f"{API}/trash/empty", headers=alex)
    assert r.status_code == 200 and r.json() == {"purged": 3}
    assert ids(client, alex, trashed="true") == []
    assert ids(client, alex, list_id=todo["id"]) == [keep["id"]]


def test_purge_expired_after_trash_days(client, alex, todo, db, monkeypatch):
    old = new_item(client, alex, todo, title="Old")
    fresh = new_item(client, alex, todo, title="Fresh")
    actor = Actor(old["created_by"], Source.api)
    trash.trash_item(db, actor, old["id"], now=utcnow() - timedelta(days=31))
    client.post(f"{API}/items/{fresh['id']}/trash", headers=alex)

    assert trash.purge_expired(db, utcnow()) == 1
    db.expire_all()
    assert db.get(Item, old["id"]) is None and db.get(Item, fresh["id"]) is not None
    event = db.scalars(
        select(Event).where(Event.item_id == old["id"], Event.type == "item.purged")
    ).one()
    assert event.actor_id != db.get(Item, fresh["id"]).created_by  # the system agent

    monkeypatch.setattr(settings, "trash_days", 0)
    assert trash.purge_due_at(db.get(Item, fresh["id"])) is None
    assert trash.purge_expired(db, utcnow() + timedelta(days=999)) == 0


def test_trash_twice_and_restore_untrashed_are_no_ops(client, alex, todo, db):
    item = new_item(client, alex, todo)
    client.post(f"{API}/items/{item['id']}/trash", headers=alex)
    client.post(f"{API}/items/{item['id']}/trash", headers=alex)
    client.post(f"{API}/items/{item['id']}/restore", headers=alex)
    client.post(f"{API}/items/{item['id']}/restore", headers=alex)
    kinds = db.scalars(select(Event.type).where(Event.item_id == item["id"])).all()
    assert kinds.count("item.trashed") == 1 and kinds.count("item.restored") == 1


# --- archive filters -------------------------------------------------------------------------


def test_archived_closed_and_order_filters(client, alex, todo):
    a = new_item(client, alex, todo, title="A")
    b = new_item(client, alex, todo, title="B")
    c = new_item(client, alex, todo, title="C")
    client.patch(f"{API}/items/{a['id']}", json={"status": "Done"}, headers=alex)
    client.patch(f"{API}/items/{b['id']}", json={"status": "Done"}, headers=alex)
    client.post(f"{API}/items/{c['id']}/archive", headers=alex)

    assert set(ids(client, alex, closed="true")) == {a["id"], b["id"]}
    assert ids(client, alex, closed="false") == []
    assert ids(client, alex, archived="true") == [c["id"]]
    assert set(ids(client, alex, archived="false")) == {a["id"], b["id"]}
    # Newest completed first.
    assert ids(client, alex, closed="true", order="completed") == [b["id"], a["id"]]
    assert ids(client, alex, order="created", include_archived="true") == [
        c["id"],
        b["id"],
        a["id"],
    ]


@pytest.mark.parametrize("order", ["sideways", "trash"])
def test_bad_order_is_refused(client, alex, order):
    assert client.get(f"{API}/items", params={"order": order}, headers=alex).status_code == 422


def test_bulk_trash_and_restore(client, alex, todo):
    a = new_item(client, alex, todo, title="A")
    b = new_item(client, alex, todo, title="B")
    sub = new_item(client, alex, todo, title="Sub", parent_id=a["id"])
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [a["id"], b["id"]], "changes": {"trash": True}},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    assert all(i["trashed_at"] for i in r.json())
    assert set(ids(client, alex, trashed="true")) == {a["id"], b["id"], sub["id"]}
    assert ids(client, alex, list_id=todo["id"]) == []

    # trash cannot ride along with other changes
    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [a["id"]], "changes": {"trash": False, "priority": "high"}},
        headers=alex,
    )
    assert r.status_code == 422, r.text

    r = client.post(
        f"{API}/items/bulk",
        json={"ids": [a["id"], b["id"]], "changes": {"trash": False}},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    assert not any(i["trashed_at"] for i in r.json())
    assert set(ids(client, alex, list_id=todo["id"])) == {a["id"], b["id"], sub["id"]}


# --- deep trees ------------------------------------------------------------------------------


def _tree(client, headers, todo, name="A", depth=4):
    """A top task with one subtask per level below it: [A, A1, A2, ...], `depth` levels down."""
    chain = [new_item(client, headers, todo, title=name)]
    for level in range(1, depth + 1):
        chain.append(
            new_item(client, headers, todo, title=f"{name}{level}", parent_id=chain[-1]["id"])
        )
    return chain


def test_purge_deletes_a_tree_four_levels_deep(client, alex, todo, db):
    # Rows go deepest level first; in one flush SQLite would refuse whenever a subtask's row
    # was deleted before its own subtasks (the order of ids decides), so try several trees.
    for n in range(8):
        chain = _tree(client, alex, todo, name=f"T{n}")
        client.post(f"{API}/items/{chain[0]['id']}/trash", headers=alex)
        r = client.post(f"{API}/items/{chain[0]['id']}/purge", headers=alex)
        assert r.status_code == 204, r.text
        db.expire_all()
        assert all(db.get(Item, x["id"]) is None for x in chain)


def test_empty_trash_and_auto_purge_take_deep_trees(client, alex, todo, db):
    for n in range(4):
        top = _tree(client, alex, todo, f"E{n}")[0]
        client.post(f"{API}/items/{top['id']}/trash", headers=alex)
    r = client.post(f"{API}/trash/empty", headers=alex)
    assert r.status_code == 200 and r.json() == {"purged": 20}, r.text

    actor = Actor(_tree(client, alex, todo, "X")[0]["created_by"], Source.api)
    old = [_tree(client, alex, todo, f"P{n}") for n in range(4)]
    for chain in old:
        trash.trash_item(db, actor, chain[0]["id"], now=utcnow() - timedelta(days=31))
    assert trash.purge_expired(db, utcnow()) == 20
    db.expire_all()
    assert all(db.get(Item, x["id"]) is None for chain in old for x in chain)


def test_one_tree_that_cannot_be_purged_does_not_hold_back_the_rest(
    client, alex, todo, db, monkeypatch
):
    actor = Actor(new_item(client, alex, todo)["created_by"], Source.api)
    chains = [_tree(client, alex, todo, f"Q{n}", depth=1) for n in range(3)]
    for chain in chains:
        trash.trash_item(db, actor, chain[0]["id"], now=utcnow() - timedelta(days=31))
    bad = chains[1][0]["id"]
    real = trash._purge

    def failing(cx, item):
        if item.id == bad:
            raise RuntimeError("disk full")
        return real(cx, item)

    monkeypatch.setattr(trash, "_purge", failing)
    assert trash.purge_expired(db, utcnow()) == 4
    db.expire_all()
    assert db.get(Item, bad) is not None
    assert db.get(Item, chains[0][0]["id"]) is None and db.get(Item, chains[2][0]["id"]) is None


def test_archive_reaches_every_level_and_unarchive_brings_them_back(client, alex, todo):
    chain = _tree(client, alex, todo)
    client.post(f"{API}/items/{chain[0]['id']}/archive", headers=alex)
    assert ids(client, alex, list_id=todo["id"]) == []
    client.post(f"{API}/items/{chain[0]['id']}/unarchive", headers=alex)
    assert set(ids(client, alex, list_id=todo["id"])) == {x["id"] for x in chain}


def test_bulk_archive_reaches_subtasks_like_the_single_archive(client, alex, todo):
    chain = _tree(client, alex, todo, depth=2)
    body = {"ids": [chain[0]["id"]], "changes": {"archive": True}}
    r = client.post(f"{API}/items/bulk", json=body, headers=alex)
    assert r.status_code == 200, r.text
    assert ids(client, alex, list_id=todo["id"]) == []
    body["changes"]["archive"] = False
    r = client.post(f"{API}/items/bulk", json=body, headers=alex)
    assert r.status_code == 200, r.text
    assert set(ids(client, alex, list_id=todo["id"])) == {x["id"] for x in chain}
