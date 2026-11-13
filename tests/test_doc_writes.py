"""Doc writes: tags on docs, duplicate, promote, event size, listings and downloads."""

from __future__ import annotations

from sqlalchemy import select

from conftest import API
from slate.models import Event


def _doc(client, alex, **body):
    r = client.post(f"{API}/docs", json=body, headers=alex)
    assert r.status_code == 201, r.text
    return r.json()


def _doc_events(db, doc_id, type_="doc.updated"):
    db.expire_all()
    return [
        (e.field, e.before_json, e.after_json)
        for e in db.scalars(
            select(Event).where(Event.subject_id == doc_id, Event.type == type_).order_by(Event.id)
        )
    ]


# --- tags on docs: merge and delete ------------------------------------------------------------


def test_merging_a_tag_moves_it_on_docs_and_records_the_change(client, alex, todo, db):
    doc = _doc(client, alex, title="Policy", list_id=todo["id"], tags=["insurance", "home"])
    both = _doc(client, alex, title="Both", list_id=todo["id"], tags=["insurance", "cover"])
    client.post(f"{API}/tags", json={"name": "cover"}, headers=alex)
    r = client.post(f"{API}/tags/insurance/merge", json={"into": "cover"}, headers=alex)
    assert r.status_code == 200, r.text

    got = client.get(f"{API}/docs/{doc['id']}", headers=alex).json()
    assert got["tags"] == ["cover", "home"]
    assert client.get(f"{API}/docs/{both['id']}", headers=alex).json()["tags"] == ["cover"]
    assert _doc_events(db, doc["id"])[-1] == ("tags", ["home", "insurance"], ["cover", "home"])
    assert _doc_events(db, both["id"])[-1] == ("tags", ["cover", "insurance"], ["cover"])


def test_deleting_a_tag_removes_it_from_docs_with_an_event(client, alex, todo, db):
    doc = _doc(client, alex, title="Policy", list_id=todo["id"], tags=["cover", "home"])
    r = client.delete(f"{API}/tags/cover", headers=alex)
    assert r.status_code in (200, 204), r.text
    assert client.get(f"{API}/docs/{doc['id']}", headers=alex).json()["tags"] == ["home"]
    assert _doc_events(db, doc["id"])[-1] == ("tags", ["cover", "home"], ["home"])
