"""Doc tags, the tree's kind/tag filters, and the project home's hub (pages, drawers, counts
and what needs the viewer)."""

from __future__ import annotations

from sqlalchemy import select

from slate.models import Event

API = "/api/v1"


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


def test_doc_tags_set_filter_and_record(client, alex, db):
    d = _doc(client, alex, title="Spec", tags=["Utility", "spec"])
    assert d["tags"] == ["spec", "utility"]  # lowercased, sorted
    r = client.patch(f"{API}/docs/{d['id']}", json={"tags": ["spec", "customer"]}, headers=alex)
    assert r.status_code == 200 and r.json()["tags"] == ["customer", "spec"]
    other = _doc(client, alex, title="Other")
    hits = client.get(f"{API}/docs", params={"tag": "spec"}, headers=alex).json()
    assert [h["id"] for h in hits] == [d["id"]] and hits[0]["tags"] == ["customer", "spec"]
    assert other["id"] in {x["id"] for x in client.get(f"{API}/docs", headers=alex).json()}
    db.expire_all()
    evs = db.scalars(
        select(Event).where(Event.subject_id == d["id"], Event.field == "tags").order_by(Event.id)
    ).all()
    assert [e.type for e in evs] == ["doc.updated", "doc.updated"]
    assert evs[1].before_json == ["spec", "utility"] and evs[1].after_json == ["customer", "spec"]
    # Same set again: no change, no event.
    client.patch(f"{API}/docs/{d['id']}", json={"tags": ["customer", "spec"]}, headers=alex)
    assert len(client.get(f"{API}/docs/{d['id']}/activity", headers=alex).json()["events"]) == 3
    # Tag usage counts include docs.
    tags = {t["name"]: t for t in client.get(f"{API}/tags", headers=alex).json()}
    assert tags["spec"]["item_count"] == 1 and tags["utility"]["item_count"] == 0


def test_tree_filters_by_kind(client, alex, todo):
    page = _doc(client, alex, title="Plan", list_id=todo["id"])
    spec = _doc(client, alex, title="Spec", kind="file", parent_id=page["id"])
    _upload(client, alex, spec["id"], b"%PDF", "spec.pdf", "first")
    files = client.get(f"{API}/docs", params={"kind": "file"}, headers=alex).json()
    assert [d["id"] for d in files] == [spec["id"]]
    pages = client.get(f"{API}/docs", params={"kind": "page"}, headers=alex).json()
    assert [d["id"] for d in pages] == [page["id"]]
    assert client.get(f"{API}/docs", params={"kind": "video"}, headers=alex).status_code == 422


def test_section_hub_pages_needs_you_and_counts(client, alex, make_account, todo):
    _, agent = make_account("agent")
    sec = todo["section_id"]
    spec = _doc(client, alex, title="Spec", kind="file", section_id=sec)
    _upload(client, alex, spec["id"], b"%PDF", "spec.pdf", "first draft")
    plan = _doc(client, alex, title="Plan", list_id=todo["id"], body_md="Seat 12 guests.")
    elsewhere = _doc(client, alex, title="Workspace note")
    # The agent asks Alex something on the spec and leaves an open review thread on the plan.
    ask = client.post(
        f"{API}/docs/{spec['id']}/comments",
        json={"body_md": "Is rev 1 the one the venue signed?", "assignee": "alex"},
        headers=agent,
    ).json()
    thread = client.post(
        f"{API}/docs/{plan['id']}/comments",
        json={"body_md": "14?", "anchor": {"quote": "Seat 12 guests."}},
        headers=agent,
    ).json()
    # Alex commented on the plan too, so its open thread is on a doc they touched.
    client.post(f"{API}/docs/{plan['id']}/comments", json={"body_md": "Checking"}, headers=alex)

    hub = client.get(f"{API}/sections/{sec}/documents", headers=alex).json()
    # Top-level docs outside any drawer and any list: the spec (section level) counts, the plan
    # (in the Todo task list) and the workspace note do not.
    page_ids = [d["id"] for d in hub["pages"]]
    assert page_ids == [spec["id"]] and elsewhere["id"] not in page_ids
    assert hub["drawers"] == []

    needs = {(n["kind"], n["comment"]["id"]) for n in hub["needs_you"]}
    assert needs == {("ask", ask["id"]), ("thread", thread["id"])}
    for n in hub["needs_you"]:
        assert n["doc"]["id"] in (spec["id"], plan["id"]) and n["url"].endswith(
            f"?comment={n['comment']['id']}"
        )
    # The agent has nothing asked of it and did not touch a doc with someone else's open thread.
    assert client.get(f"{API}/sections/{sec}/documents", headers=agent).json()["needs_you"] == []

    assert "recent" not in hub and "activity" not in hub  # no feed on a project home
    assert hub["counts"] == {"docs": 2, "files": 1, "open_threads": 1}
    # Only explicit resolution clears Needs you; replying is not completion.
    client.post(f"{API}/doc-comments/{thread['id']}/resolve", headers=alex)
    client.post(
        f"{API}/docs/{spec['id']}/comments",
        json={"body_md": "Yes", "parent_id": ask["id"]},
        headers=alex,
    )
    assert len(client.get(f"{API}/sections/{sec}/documents", headers=alex).json()["needs_you"]) == 1
    client.post(f"{API}/doc-comments/{ask['id']}/resolve", headers=alex)
    assert client.get(f"{API}/sections/{sec}/documents", headers=alex).json()["needs_you"] == []


def test_hub_follows_section_access(client, alex, make_account):
    sec = client.post(
        f"{API}/sections", json={"name": "Private", "audience": "work"}, headers=alex
    ).json()
    client.patch(f"{API}/sections/{sec['id']}", json={"private": True}, headers=alex)
    _, other = make_account("other", kind="person")
    assert client.get(f"{API}/sections/{sec['id']}/documents", headers=other).status_code == 404
