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


def _file_doc(client, alex, title, data, filename="q.csv", ctype="text/csv", **body):
    d = _doc(client, alex, kind="file", title=title, **body)
    r = client.post(
        f"{API}/docs/{d['id']}/revisions",
        params={"filename": filename, "note": "first"},
        content=data,
        headers={**alex, "Content-Type": ctype},
    )
    assert r.status_code == 201, r.text
    return d


class _Statements:
    """SQL statements the engine runs while the block is open."""

    def __enter__(self):
        from sqlalchemy import event

        from slate.db import engine

        self.sql: list[str] = []
        self._engine = engine
        self._listen = lambda *a: self.sql.append(a[2])
        event.listen(engine, "before_cursor_execute", self._listen)
        return self

    def __exit__(self, *exc):
        from sqlalchemy import event

        event.remove(self._engine, "before_cursor_execute", self._listen)


# --- duplicate ---------------------------------------------------------------------------------


def test_duplicate_reads_only_the_copied_docs_and_never_reextracts(client, alex, todo, monkeypatch):
    from slate.service import doc_compare

    for n in range(3):  # unrelated files elsewhere in the workspace
        _file_doc(client, alex, f"Other {n}", b"a,b\n1,2\n")
    page = _doc(client, alex, title="Notes", list_id=todo["id"], body_md="hello")
    sheet = _file_doc(client, alex, "Quote", b"item,price\nbolt,0.25\n", parent_id=page["id"])

    def never(*_a, **_k):
        raise AssertionError("a copy has the same bytes; its text and tables are copied")

    monkeypatch.setattr(doc_compare, "extract", never)

    with _Statements() as st:
        r = client.post(
            f"{API}/docs/{page['id']}/duplicate", json={"subpages": False}, headers=alex
        )
    assert r.status_code == 201, r.text
    assert not [s for s in st.sql if "doc_files.data" in s]

    with _Statements() as st:
        r = client.post(f"{API}/docs/{page['id']}/duplicate", json={}, headers=alex)
    assert r.status_code == 201, r.text
    blob_reads = [s for s in st.sql if "doc_files.data" in s]
    assert len(blob_reads) == 1  # the one file in the copied subtree

    copy_kid = r.json()["children"][0]
    assert copy_kid["title"] == "Quote"
    hits = client.get(f"{API}/docs", params={"q": "bolt"}, headers=alex).json()
    assert {h["id"] for h in hits} >= {sheet["id"], copy_kid["id"]}
    out = client.get(
        f"{API}/docs/{copy_kid['id']}/revisions/compare", params={"from": 1}, headers=alex
    )
    assert out.status_code == 200, out.text


# --- revisions --------------------------------------------------------------------------------


def test_an_upload_that_lands_while_another_is_read_takes_the_next_number(
    client, alex, monkeypatch
):
    """Two uploads to one file doc: the second one's number is chosen when it writes, so an
    upload committed after it looked at the history does not make it collide."""
    from slate.db import SessionLocal
    from slate.models import Source
    from slate.service import docs as docs_svc
    from slate.service.core import Actor

    d = _file_doc(client, alex, "Quote", b"a,b\n1,2\n")
    me = client.get(f"{API}/me", headers=alex).json()
    actor = Actor(account_id=me["account"]["id"], source=Source.api)
    real = docs_svc._revision_row
    raced = []

    def racing(*a, **k):
        if not raced:  # the other upload commits between this one's read and its write
            raced.append(1)
            with SessionLocal() as other:
                docs_svc.add_revision(other, actor, d["id"], filename="b.csv", data=b"b", note="b")
        return real(*a, **k)

    monkeypatch.setattr(docs_svc, "_revision_row", racing)
    with SessionLocal() as mine:
        rev = docs_svc.add_revision(mine, actor, d["id"], filename="a.csv", data=b"a", note="a")
    assert rev.revision_no == 3
    revs = client.get(f"{API}/docs/{d['id']}/revisions", headers=alex).json()
    assert [(r["revision_no"], r["note"]) for r in revs] == [(3, "a"), (2, "b"), (1, "first")]
    assert [r["superseded_at"] is None for r in revs] == [True, False, False]


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
