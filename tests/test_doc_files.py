"""File docs: an uploaded file (PDF, spreadsheet, photo) as a doc of kind `file`, in the same tree
as pages, with linear revisions that are never deleted."""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from slate.models import DocFile, Event

API = "/api/v1"
PDF = b"%PDF-1.4\n" + b"x" * 40


def _file_doc(client, h, **body):
    r = client.post(f"{API}/docs", json={"kind": "file", **body}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, h, doc_id, data, filename, note, status=201, ctype="application/pdf"):
    r = client.post(
        f"{API}/docs/{doc_id}/revisions",
        params={"filename": filename, "note": note},
        content=data,
        headers={**h, "Content-Type": ctype},
    )
    assert r.status_code == status, r.text
    return r.json()


def test_a_file_doc_lives_in_the_tree_and_its_uploads_are_revisions(client, alex, todo, db):
    folder = client.post(
        f"{API}/docs", json={"title": "Contracts", "list_id": todo["id"]}, headers=alex
    ).json()
    d = _file_doc(client, alex, title="Venue contract", parent_id=folder["id"])
    assert d["kind"] == "file" and d["file"] is None and d["revisions"] == []

    r1 = _upload(client, alex, d["id"], PDF, "venue.pdf", "First draft")
    assert r1["revision_no"] == 1 and r1["note"] == "First draft"
    assert r1["superseded_at"] is None
    r2 = _upload(client, alex, d["id"], PDF + b"2", "venue-v2.pdf", "Updated the dates on page 2")
    assert r2["revision_no"] == 2

    got = client.get(f"{API}/docs/{d['id']}", headers=alex).json()
    assert got["file"]["revision_no"] == 2 and got["file"]["filename"] == "venue-v2.pdf"
    assert [r["revision_no"] for r in got["revisions"]] == [2, 1]
    assert got["revisions"][1]["superseded_at"] is not None
    assert "data" not in got["revisions"][0]

    # Both revisions still download; the tree lists the file with its current revision.
    old = client.get(f"{API}/doc-files/{r1['id']}", headers=alex)
    assert old.content == PDF and old.headers["content-type"] == "application/pdf"
    listed = {x["id"]: x for x in client.get(f"{API}/docs", headers=alex).json()}
    assert listed[d["id"]]["kind"] == "file" and listed[d["id"]]["file"]["revision_no"] == 2
    assert listed[folder["id"]]["kind"] == "page" and listed[folder["id"]]["file"] is None

    db.expire_all()
    ev = db.scalars(
        select(Event).where(Event.type == "doc.revision_added").order_by(Event.id)
    ).all()
    assert [e.after_json["revision_no"] for e in ev] == [1, 2]
    assert (
        ev[1].after_json["note"] == "Updated the dates on page 2" and "data" not in ev[1].after_json
    )
    assert ev[1].subject_id == d["id"]


def test_the_note_is_required_and_pages_take_no_revisions(client, alex):
    d = _file_doc(client, alex, title="Spec")
    r = client.post(
        f"{API}/docs/{d['id']}/revisions",
        params={"filename": "spec.pdf"},
        content=PDF,
        headers={**alex, "Content-Type": "application/pdf"},
    )
    assert r.status_code == 422
    assert _upload(client, alex, d["id"], PDF, "spec.pdf", "   ", status=422)
    page = client.post(f"{API}/docs", json={"title": "Notes"}, headers=alex).json()
    assert page["kind"] == "page"
    assert "page" in _upload(client, alex, page["id"], PDF, "x.pdf", "n", status=422)["detail"]
    assert _upload(client, alex, d["id"], b"", "spec.pdf", "n", status=422)


def test_restore_is_a_new_revision_and_history_stays(client, alex):
    d = _file_doc(client, alex, title="Quote")
    _upload(client, alex, d["id"], b"v1", "quote.csv", "first", ctype="text/csv")
    _upload(client, alex, d["id"], b"v2", "quote.csv", "second", ctype="text/csv")
    r = client.post(f"{API}/docs/{d['id']}/revisions/1/restore", headers=alex)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["file"]["revision_no"] == 3 and got["file"]["note"] == "Restored revision 1"
    assert client.get(f"{API}/doc-files/{got['file']['id']}", headers=alex).content == b"v1"
    assert [x["revision_no"] for x in got["revisions"]] == [3, 2, 1]
    assert (
        client.get(f"{API}/docs/{d['id']}/revisions", headers=alex).json()[0]["restored_from"] == 1
    )
    assert client.post(f"{API}/docs/{d['id']}/revisions/9/restore", headers=alex).status_code == 404


def test_base64_upload_for_agents(client, alex):
    d = _file_doc(client, alex, title="Photo")
    r = client.post(
        f"{API}/docs/{d['id']}/revisions/base64",
        json={
            "filename": "venue.png",
            "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 10).decode(),
            "note": "Taken at the venue",
        },
        headers=alex,
    )
    assert r.status_code == 201, r.text
    assert r.json()["content_type"] == "image/png" and r.json()["revision_no"] == 1


def test_a_file_doc_keeps_a_description_and_can_be_moved_duplicated_and_archived(
    client, alex, todo
):
    d = _file_doc(client, alex, title="Venue photos", body_md="Taken 10/30.")
    _upload(client, alex, d["id"], b"img", "a.jpg", "n", ctype="image/jpeg")
    r = client.patch(f"{API}/docs/{d['id']}", json={"body_md": "Taken 10/31."}, headers=alex)
    assert r.status_code == 200 and r.json()["body_md"] == "Taken 10/31."
    assert r.json()["kind"] == "file"
    r = client.post(f"{API}/docs/{d['id']}/move", json={"list_id": todo["id"]}, headers=alex)
    assert r.status_code == 200 and r.json()["list_id"] == todo["id"]
    copy = client.post(f"{API}/docs/{d['id']}/duplicate", json={}, headers=alex).json()
    assert copy["kind"] == "file" and copy["file"]["revision_no"] == 1
    assert copy["file"]["id"] != r.json()["file"]["id"]
    assert client.get(f"{API}/doc-files/{copy['file']['id']}", headers=alex).content == b"img"
    assert client.post(f"{API}/docs/{d['id']}/archive", headers=alex).status_code == 200
    assert d["id"] not in {x["id"] for x in client.get(f"{API}/docs", headers=alex).json()}
    assert client.delete(f"{API}/docs/{d['id']}", headers=alex).status_code == 200


def test_search_finds_a_file_by_its_filename(client, alex):
    d = _file_doc(client, alex, title="Quote")
    _upload(client, alex, d["id"], b"x", "example-quote-2026.pdf", "n")
    hits = client.get(f"{API}/docs", params={"q": "example-quote"}, headers=alex).json()
    assert [h["id"] for h in hits] == [d["id"]]


def test_promote_a_task_attachment_into_the_library(client, alex, todo, db):
    item = client.post(
        f"{API}/items", json={"title": "Review contract", "list_id": todo["id"]}, headers=alex
    ).json()
    att = client.post(
        f"{API}/items/{item['id']}/attachments",
        params={"filename": "contract.pdf"},
        content=PDF,
        headers={**alex, "Content-Type": "application/pdf"},
    ).json()
    r = client.post(
        f"{API}/attachments/{att['id']}/promote",
        json={"section_id": todo["section_id"], "note": "From the card"},
        headers=alex,
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["kind"] == "file" and doc["title"] == "contract.pdf"
    assert doc["section_id"] == todo["section_id"] and doc["file"]["note"] == "From the card"
    assert client.get(f"{API}/doc-files/{doc['file']['id']}", headers=alex).content == PDF
    # The attachment is gone from the card; a comment on the card says where it went.
    assert client.get(f"{API}/attachments/{att['id']}", headers=alex).status_code == 404
    comments = client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    assert f"[[doc:{doc['id']}]]" in comments[-1]["body_md"]
    db.expire_all()
    assert db.scalar(select(DocFile).where(DocFile.doc_id == doc["id"])).revision_no == 1


def test_export_and_health_see_file_docs(client, alex, todo):
    d = _file_doc(client, alex, title="Spec", list_id=todo["id"])
    _upload(client, alex, d["id"], PDF, "spec.pdf", "n")
    out = client.get(f"{API}/export", params={"list_id": todo["id"]}, headers=alex).json()
    doc = next(x for x in out["docs"] if x["id"] == d["id"])
    assert doc["kind"] == "file" and doc["file"]["filename"] == "spec.pdf"
    assert "data" not in str(out)
    report = client.get(f"{API}/health", headers=alex).json()
    assert report["db"]["blob_bytes"] >= len(PDF)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("2026-10-30 - Kickoff.pdf", "2026-10-30"),
        ("Quote 10-30-26.xlsx", "2026-10-30"),
        ("venue_photos_20261030.zip", "2026-10-30"),
        ("Invoice 10.30.2026 final", "2026-10-30"),
        ("Meeting Oct 30, 2026 notes", "2026-10-30"),
        ("30 October 2026 agenda.docx", "2026-10-30"),
        ("spec_v3.pdf", None),
        ("2026-13-45 bad.pdf", None),
        ("order 1234567.pdf", None),
    ],
)
def test_the_date_in_a_filename(name, expected):
    from slate.service.docs import date_in_name

    got = date_in_name(name)
    assert (got.isoformat() if got else None) == expected


def test_file_docs_are_dated_from_their_names(client, alex, todo):
    """A new file doc named with a date is created on that date (noon Chicago)."""
    dated = _file_doc(client, alex, title="2026-10-30 - Kickoff", list_id=todo["id"])
    assert dated["created_at"].startswith("2026-10-30T1")  # noon Chicago is 17:00 or 18:00 UTC
    plain = _file_doc(client, alex, title="Guest list", list_id=todo["id"])
    assert plain["created_at"][:10] != "2026-10-30"
