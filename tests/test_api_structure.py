"""Sections, lists, statuses and tags over /api/v1."""

from __future__ import annotations

from conftest import API


def _section(client, h, name="Personal", audience="personal"):
    return client.post(f"{API}/sections", json={"name": name, "audience": audience}, headers=h)


# --- sections ------------------------------------------------------------------------------


def test_create_and_list_sections(client, alex):
    r = _section(client, alex)
    assert r.status_code == 201
    assert r.json()["slug"] == "personal"
    _section(client, alex, "AI", "ai")
    names = [s["name"] for s in client.get(f"{API}/sections", headers=alex).json()]
    assert names == ["Personal", "AI"]


def test_duplicate_section_is_409(client, alex):
    _section(client, alex)
    assert _section(client, alex, "personal").status_code == 409


def test_patch_section_renames_and_archives(client, alex):
    sid = _section(client, alex).json()["id"]
    r = client.patch(f"{API}/sections/{sid}", json={"name": "Home Life"}, headers=alex)
    assert r.json()["slug"] == "home-life"
    r = client.patch(f"{API}/sections/{sid}", json={"archived": True}, headers=alex)
    assert r.json()["archived_at"] is not None
    assert client.get(f"{API}/sections", headers=alex).json() == []
    shown = client.get(f"{API}/sections?include_archived=true", headers=alex).json()
    assert [s["id"] for s in shown] == [sid]
    r = client.patch(f"{API}/sections/{sid}", json={"archived": False}, headers=alex)
    assert r.json()["archived_at"] is None


def test_unknown_section_is_404(client, alex):
    assert client.patch(f"{API}/sections/nope", json={"name": "x"}, headers=alex).status_code == 404


def test_reads_need_a_token_and_writes_need_write_scope(client, alex, make_account):
    assert client.get(f"{API}/sections").status_code == 401
    _, reader = make_account("reader", ("read",))
    assert client.get(f"{API}/sections", headers=reader).status_code == 200
    assert _section(client, reader).status_code == 403


# --- lists ---------------------------------------------------------------------------------


def test_new_list_gets_to_do_in_progress_done(client, alex, todo):
    statuses = todo["statuses"]
    assert [(s["name"], s["kind"]) for s in statuses] == [
        ("To do", "open"),
        ("In progress", "active"),
        ("Done", "done"),
    ]
    assert todo["default_status_id"] == statuses[0]["id"]


def test_list_with_custom_statuses(client, alex):
    sid = _section(client, alex, "AI", "ai").json()["id"]
    r = client.post(
        f"{API}/lists",
        json={
            "section_id": sid,
            "name": "Work Queue",
            "statuses": [
                {"name": "inbox"},
                {"name": "ready", "kind": "active"},
                {"name": "shipping", "kind": "done"},
            ],
        },
        headers=alex,
    )
    assert r.status_code == 201
    assert [s["name"] for s in r.json()["statuses"]] == ["inbox", "ready", "shipping"]
    assert r.json()["slug"] == "work-queue"


def test_list_in_unknown_section_is_404_and_duplicate_is_409(client, alex, todo):
    assert (
        client.post(f"{API}/lists", json={"section_id": "nope", "name": "X"}, headers=alex)
    ).status_code == 404
    dup = client.post(
        f"{API}/lists", json={"section_id": todo["section_id"], "name": "todo"}, headers=alex
    )
    assert dup.status_code == 409


def test_get_lists_by_section_and_by_id(client, alex, todo):
    other = _section(client, alex, "Work", "work").json()["id"]
    client.post(f"{API}/lists", json={"section_id": other, "name": "Mirrored"}, headers=alex)
    found = client.get(f"{API}/lists?section_id={todo['section_id']}", headers=alex).json()
    assert [lst["name"] for lst in found] == ["Todo"]
    assert len(client.get(f"{API}/lists", headers=alex).json()) == 2
    assert client.get(f"{API}/lists/{todo['id']}", headers=alex).json()["name"] == "Todo"
    assert client.get(f"{API}/lists/nope", headers=alex).status_code == 404


def test_patch_list_rename_default_status_and_archive(client, alex, todo):
    lid = todo["id"]
    r = client.patch(
        f"{API}/lists/{lid}",
        json={"name": "Inbox", "default_status": "In progress"},
        headers=alex,
    )
    assert r.json()["slug"] == "inbox"
    assert r.json()["default_status_id"] == todo["statuses"][1]["id"]
    client.patch(f"{API}/lists/{lid}", json={"archived": True}, headers=alex)
    assert client.get(f"{API}/lists", headers=alex).json() == []
    assert len(client.get(f"{API}/lists?include_archived=true", headers=alex).json()) == 1


def test_patch_list_moves_it_to_another_section(client, alex, todo):
    work = _section(client, alex, "Work", "work").json()["id"]
    client.post(f"{API}/lists", json={"section_id": work, "name": "Inbox"}, headers=alex)
    item = client.post(
        f"{API}/items", json={"list_id": todo["id"], "title": "x"}, headers=alex
    ).json()
    r = client.patch(f"{API}/lists/{todo['id']}", json={"section_id": work}, headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["section_id"] == work
    assert r.json()["position"] == 1  # the end of Work
    names = [
        lst["name"] for lst in client.get(f"{API}/lists?section_id={work}", headers=alex).json()
    ]
    assert names == ["Inbox", "Todo"]
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["list_id"] == todo["id"]  # items move with it
    # With a position, in one change; same section is a no-op on section_id.
    r = client.patch(
        f"{API}/lists/{todo['id']}", json={"section_id": work, "position": 0}, headers=alex
    )
    assert r.json()["position"] == 0
    # A slug already taken in the target section is refused, and nothing changes.
    personal = todo["section_id"]
    client.post(f"{API}/lists", json={"section_id": personal, "name": "Todo"}, headers=alex)
    r = client.patch(f"{API}/lists/{todo['id']}", json={"section_id": personal}, headers=alex)
    assert r.status_code == 409
    assert client.get(f"{API}/lists/{todo['id']}", headers=alex).json()["section_id"] == work
    r = client.patch(f"{API}/lists/{todo['id']}", json={"section_id": "nope"}, headers=alex)
    assert r.status_code == 404


def test_bad_default_status_is_422(client, alex, todo):
    r = client.patch(f"{API}/lists/{todo['id']}", json={"default_status": "nope"}, headers=alex)
    assert r.status_code == 422
    assert "no status" in r.json()["detail"]


# --- statuses ------------------------------------------------------------------------------


def _item(client, h, list_id, **fields):
    r = client.post(f"{API}/items", json={"list_id": list_id, "title": "x", **fields}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_put_statuses_keeps_by_id_renames_and_adds(client, alex, todo):
    open_, active, done = todo["statuses"]
    item = _item(client, alex, todo["id"], status="In progress")
    r = client.put(
        f"{API}/lists/{todo['id']}/statuses",
        json={
            "statuses": [
                {"id": open_["id"], "name": "todo"},
                {"id": active["id"], "name": "in progress", "kind": "active"},
                {"name": "review", "kind": "active"},
                {"name": "done", "kind": "done"},
            ]
        },
        headers=alex,
    )
    assert r.status_code == 200, r.text
    after = r.json()["statuses"]
    assert [s["name"] for s in after] == ["todo", "in progress", "review", "done"]
    assert after[1]["id"] == active["id"] and after[3]["id"] == done["id"]
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["status"]["name"] == "in progress"


def test_put_statuses_refuses_to_strand_items_without_fallback(client, alex, todo):
    _item(client, alex, todo["id"], status="In progress")
    r = client.put(
        f"{API}/lists/{todo['id']}/statuses",
        json={"statuses": [{"name": "open"}, {"name": "done", "kind": "done"}]},
        headers=alex,
    )
    assert r.status_code == 422
    assert "fallback" in r.json()["detail"]
    statuses = client.get(f"{API}/lists/{todo['id']}", headers=alex).json()["statuses"]
    assert len(statuses) == 3  # nothing changed


def test_put_statuses_moves_stranded_items_to_fallback(client, alex, todo):
    item = _item(client, alex, todo["id"], status="done")
    assert item["completed_at"] is not None
    r = client.put(
        f"{API}/lists/{todo['id']}/statuses",
        json={
            "statuses": [{"name": "open"}, {"name": "closed", "kind": "done"}],
            "fallback": "open",
        },
        headers=alex,
    )
    assert r.status_code == 200, r.text
    got = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert got["status"]["name"] == "open"
    assert got["completed_at"] is None  # left done, so cleared
    types = [e["type"] for e in got["events"]]
    assert "item.status_changed" in types


def test_put_statuses_unknown_fallback_is_422(client, alex, todo):
    r = client.put(
        f"{API}/lists/{todo['id']}/statuses",
        json={"statuses": [{"name": "open"}], "fallback": "nope"},
        headers=alex,
    )
    assert r.status_code == 422


# --- tags ----------------------------------------------------------------------------------


def test_tags_create_lowercase_list_and_conflict(client, alex):
    r = client.post(f"{API}/tags", json={"name": "  Needs-Alex "}, headers=alex)
    assert r.status_code == 201
    assert r.json()["name"] == "needs-alex"
    assert client.post(f"{API}/tags", json={"name": "NEEDS-ALEX"}, headers=alex).status_code == 409
    client.post(f"{API}/tags", json={"name": "ai project"}, headers=alex)
    assert [t["name"] for t in client.get(f"{API}/tags", headers=alex).json()] == [
        "ai project",
        "needs-alex",
    ]


def test_tag_rename(client, alex, todo):
    item = _item(client, alex, todo["id"], tags=["h3"])
    r = client.patch(f"{API}/tags/h3", json={"name": "H4"}, headers=alex)
    assert r.status_code == 200 and r.json()["name"] == "h4"
    assert client.get(f"{API}/items/{item['id']}", headers=alex).json()["tags"] == ["h4"]
    client.post(f"{API}/tags", json={"name": "ai"}, headers=alex)
    assert client.patch(f"{API}/tags/h4", json={"name": "ai"}, headers=alex).status_code == 409
    assert client.patch(f"{API}/tags/nope", json={"name": "x"}, headers=alex).status_code == 404
