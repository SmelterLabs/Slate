"""Drawers: a list whose entries are documents, not tasks. A section holds Lists, Drawers and
Pages. A drawer has folders (docs of kind `folder`), its documents can pin one revision ("the
one in use"), a page can be turned into a drawer, and the project home's hub lists the
section's drawers with counts plus what needs the viewer."""

from __future__ import annotations

from sqlalchemy import select

from slate.models import Event

API = "/api/v1"


def _drawer(client, h, section_id, name="Contracts", status=201, **body):
    r = client.post(
        f"{API}/lists",
        json={"section_id": section_id, "name": name, "kind": "drawer", **body},
        headers=h,
    )
    assert r.status_code == status, r.text
    return r.json()


def _doc(client, h, **body):
    r = client.post(f"{API}/docs", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, h, doc_id, data, filename, note, ctype="application/pdf"):
    r = client.post(
        f"{API}/docs/{doc_id}/revisions",
        params={"filename": filename, "note": note},
        content=data,
        headers={**h, "Content-Type": ctype},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_drawer_is_a_list_of_documents_and_takes_no_tasks(client, alex, todo, db):
    sec = todo["section_id"]
    d = _drawer(client, alex, sec)
    assert d["kind"] == "drawer" and d["section_id"] == sec
    assert [
        x["kind"] for x in client.get(f"{API}/lists", headers=alex).json() if x["id"] == todo["id"]
    ] == ["tasks"]
    r = client.post(f"{API}/items", json={"title": "Nope", "list_id": d["id"]}, headers=alex)
    assert r.status_code == 422 and "drawer" in r.json()["detail"]
    # Nor can a task be moved into one.
    item = client.post(
        f"{API}/items", json={"title": "T", "list_id": todo["id"]}, headers=alex
    ).json()
    r = client.patch(f"{API}/items/{item['id']}", json={"list_id": d["id"]}, headers=alex)
    assert r.status_code == 422
    db.expire_all()
    ev = db.scalars(
        select(Event).where(Event.type == "list.created").order_by(Event.id.desc())
    ).first()
    assert ev.after_json["kind"] == "drawer"


def test_folders_entries_and_the_drawer_listing(client, alex, todo):
    d = _drawer(client, alex, todo["section_id"])
    folder = _doc(client, alex, kind="folder", title="Vendors", list_id=d["id"])
    assert folder["kind"] == "folder"
    catering = _doc(client, alex, kind="file", title="Catering", parent_id=folder["id"])
    _upload(client, alex, catering["id"], b"%PDF-1", "catering.pdf", "first")
    _doc(client, alex, title="Overview", list_id=d["id"], body_md="Hello")
    _doc(client, alex, kind="file", title="Guest list", list_id=d["id"])

    top = client.get(f"{API}/lists/{d['id']}/drawer", headers=alex).json()
    assert top["list"]["id"] == d["id"] and top["folder"] is None and top["path"] == []
    assert [(e["kind"], e["title"]) for e in top["entries"]] == [
        ("folder", "Vendors"),
        ("page", "Overview"),
        ("file", "Guest list"),
    ]
    assert top["entries"][0]["children_count"] == 1
    assert top["counts"] == {"docs": 3, "files": 2, "folders": 1, "open_threads": 0}

    inside = client.get(
        f"{API}/lists/{d['id']}/drawer", params={"folder": folder["id"]}, headers=alex
    ).json()
    assert inside["folder"]["id"] == folder["id"] and [p["id"] for p in inside["path"]] == [
        folder["id"]
    ]
    assert [e["id"] for e in inside["entries"]] == [catering["id"]]
    assert inside["entries"][0]["file"]["revision_no"] == 1

    # A folder takes no revisions, no body; another drawer's folder is refused.
    r = client.post(
        f"{API}/docs/{folder['id']}/revisions",
        params={"filename": "x.pdf", "note": "n"},
        content=b"x",
        headers={**alex, "Content-Type": "application/pdf"},
    )
    assert r.status_code == 422
    other = _drawer(client, alex, todo["section_id"], name="Other")
    r = client.get(
        f"{API}/lists/{other['id']}/drawer", params={"folder": folder["id"]}, headers=alex
    )
    assert r.status_code == 422
    # A task list has no drawer listing.
    assert client.get(f"{API}/lists/{todo['id']}/drawer", headers=alex).status_code == 422
    assert (
        client.get(f"{API}/docs", params={"kind": "folder"}, headers=alex).json()[0]["id"]
        == folder["id"]
    )


def test_pin_a_revision_as_the_one_in_use(client, alex, todo, db):
    d = _drawer(client, alex, todo["section_id"])
    doc = _doc(client, alex, kind="file", title="Venue contract", list_id=d["id"])
    _upload(client, alex, doc["id"], b"r1", "b.txt", "first", "text/plain")
    _upload(client, alex, doc["id"], b"r2", "b.txt", "second", "text/plain")
    _upload(client, alex, doc["id"], b"r3", "b.txt", "third", "text/plain")
    r = client.post(
        f"{API}/docs/{doc['id']}/revisions/2/pin",
        json={"note": "The signed copy"},
        headers=alex,
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["pinned"] == {
        "revision_no": 2,
        "note": "The signed copy",
        "file_id": got["revisions"][1]["id"],
    }
    assert got["file"]["revision_no"] == 3  # the current one is still the newest
    listed = client.get(f"{API}/lists/{d['id']}/drawer", headers=alex).json()["entries"][0]
    assert listed["pinned"]["revision_no"] == 2
    # Pinning another revision moves the pin; unpin clears it; a missing revision is 404.
    assert (
        client.post(f"{API}/docs/{doc['id']}/revisions/3/pin", json={}, headers=alex).json()[
            "pinned"
        ]["revision_no"]
        == 3
    )
    assert client.delete(f"{API}/docs/{doc['id']}/pin", headers=alex).json()["pinned"] is None
    assert (
        client.post(f"{API}/docs/{doc['id']}/revisions/9/pin", json={}, headers=alex).status_code
        == 404
    )
    db.expire_all()
    types = [
        e.type
        for e in db.scalars(select(Event).where(Event.subject_id == doc["id"]).order_by(Event.id))
    ]
    assert types.count("doc.revision_pinned") == 2 and types.count("doc.revision_unpinned") == 1


def test_pin_entries_to_the_top_of_a_drawer_or_folder(client, alex, todo, db):
    """A pinned entry sits first in its own listing (the drawer's top or its folder), oldest pin
    first, then the rest in tree order; it is shared; unpinning puts it back. Two events."""
    d = _drawer(client, alex, todo["section_id"])
    folder = _doc(client, alex, kind="folder", title="Vendors", list_id=d["id"])
    overview = _doc(client, alex, title="Overview", list_id=d["id"])
    guests = _doc(client, alex, kind="file", title="Guest list", list_id=d["id"])
    catering = _doc(client, alex, kind="file", title="Catering", parent_id=folder["id"])
    florist = _doc(client, alex, kind="file", title="Florist", parent_id=folder["id"])

    r = client.post(f"{API}/docs/{guests['id']}/top", headers=alex)
    assert r.status_code == 200, r.text
    assert r.json()["pinned_to_top"]["by"] is not None
    client.post(f"{API}/docs/{overview['id']}/top", headers=alex)
    top = client.get(f"{API}/lists/{d['id']}/drawer", headers=alex).json()
    assert [e["title"] for e in top["entries"]] == ["Guest list", "Overview", "Vendors"]
    assert top["entries"][0]["pinned_to_top"] and top["entries"][2]["pinned_to_top"] is None

    # Inside the folder, its own pins; pinning twice is a no-op.
    client.post(f"{API}/docs/{florist['id']}/top", headers=alex)
    client.post(f"{API}/docs/{florist['id']}/top", headers=alex)
    inside = client.get(
        f"{API}/lists/{d['id']}/drawer", params={"folder": folder["id"]}, headers=alex
    ).json()
    assert [e["id"] for e in inside["entries"]] == [florist["id"], catering["id"]]

    assert (
        client.delete(f"{API}/docs/{guests['id']}/top", headers=alex).json()["pinned_to_top"]
        is None
    )
    top = client.get(f"{API}/lists/{d['id']}/drawer", headers=alex).json()
    assert [e["title"] for e in top["entries"]] == ["Overview", "Vendors", "Guest list"]
    types = [e.type for e in db.scalars(select(Event).order_by(Event.id))]
    assert types.count("doc.pinned_to_top") == 3 and types.count("doc.unpinned_from_top") == 1
    assert client.get(f"{API}/docs/{guests['id']}", headers=alex).json()["pinned_to_top"] is None


def test_turn_a_page_into_a_drawer_and_convert_lists(client, alex, todo):
    sec = todo["section_id"]
    meetings = _doc(client, alex, title="Meetings", section_id=sec, body_md="Transcripts.")
    t1 = _doc(client, alex, title="2026-10-29 - review", parent_id=meetings["id"], body_md="...")
    t2 = _doc(client, alex, title="2026-10-30 - plan", parent_id=meetings["id"], body_md="...")
    r = client.post(f"{API}/docs/{meetings['id']}/to-drawer", headers=alex)
    assert r.status_code == 201, r.text
    drawer = r.json()
    assert (
        drawer["kind"] == "drawer" and drawer["name"] == "Meetings" and drawer["section_id"] == sec
    )
    entries = client.get(f"{API}/lists/{drawer['id']}/drawer", headers=alex).json()["entries"]
    assert [e["id"] for e in entries] == [meetings["id"], t1["id"], t2["id"]]
    assert all(e["parent_id"] is None for e in entries)
    # The page itself stays, now at the top of the drawer, so its text is not lost.
    assert (
        client.get(f"{API}/docs/{meetings['id']}", headers=alex).json()["list_id"] == drawer["id"]
    )
    # A subpage cannot be turned into a drawer (t1 is a top entry now; make a fresh subpage).
    top = _doc(client, alex, title="Notes", section_id=sec)
    sub = _doc(client, alex, title="Detail", parent_id=top["id"])
    r = client.post(f"{API}/docs/{sub['id']}/to-drawer", headers=alex)
    assert r.status_code == 422

    # Converting lists: an empty task list becomes a drawer; one with open tasks is refused.
    empty = client.post(
        f"{API}/lists", json={"section_id": sec, "name": "Spare"}, headers=alex
    ).json()
    assert (
        client.patch(f"{API}/lists/{empty['id']}", json={"kind": "drawer"}, headers=alex).json()[
            "kind"
        ]
        == "drawer"
    )
    r = client.patch(f"{API}/lists/{todo['id']}", json={"kind": "drawer"}, headers=alex)
    assert r.status_code == 200  # todo has no items in this fixture
    client.patch(f"{API}/lists/{todo['id']}", json={"kind": "tasks"}, headers=alex)
    client.post(f"{API}/items", json={"title": "T", "list_id": todo["id"]}, headers=alex)
    r = client.patch(f"{API}/lists/{todo['id']}", json={"kind": "drawer"}, headers=alex)
    assert r.status_code == 422 and "task" in r.json()["detail"]


def test_hub_lists_drawers_with_counts_and_needs_you(client, alex, make_account, todo):
    _, agent = make_account("agent")
    sec = todo["section_id"]
    d = _drawer(client, alex, sec)
    spec = _doc(client, alex, kind="file", title="Spec", list_id=d["id"])
    _upload(client, alex, spec["id"], b"%PDF", "spec.pdf", "first")
    plan = _doc(
        client, alex, title="Plan", section_id=sec, body_md="Seat 12."
    )  # a section page, not in a drawer
    ask = client.post(
        f"{API}/docs/{spec['id']}/comments",
        json={"body_md": "Which rev?", "assignee": "alex"},
        headers=agent,
    ).json()
    client.post(
        f"{API}/docs/{plan['id']}/comments",
        json={"body_md": "14?", "anchor": {"quote": "Seat 12."}},
        headers=agent,
    )
    hub = client.get(f"{API}/sections/{sec}/documents", headers=alex).json()
    assert [x["list"]["id"] for x in hub["drawers"]] == [d["id"]]
    assert hub["drawers"][0]["counts"] == {"docs": 1, "files": 1, "folders": 0, "open_threads": 0}
    assert hub["drawers"][0]["needs_you"] == 1
    assert {(n["kind"], n["comment"]["id"]) for n in hub["needs_you"]} == {("ask", ask["id"])} | {
        ("thread", c["id"])
        for c in client.get(f"{API}/docs/{plan['id']}/comments", headers=alex).json()
    }
    assert hub["pages"][0]["id"] == plan["id"]
    assert "recent" not in hub and "activity" not in hub
