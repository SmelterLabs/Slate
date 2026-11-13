"""Compare two revisions of a file doc, by type:
text-like files diff line by line; CSV, TSV and XLSX diff cell by cell; PDFs diff their
extracted text (approximate, labelled so); images and the rest have no server diff (the board
shows them side by side). Each revision's text is extracted once on upload so search finds it."""

from __future__ import annotations

import io

import openpyxl
from pypdf import PdfWriter

API = "/api/v1"


def _file_doc(client, h, **body):
    r = client.post(f"{API}/docs", json={"kind": "file", **body}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, h, doc_id, data, filename, note, ctype):
    r = client.post(
        f"{API}/docs/{doc_id}/revisions",
        params={"filename": filename, "note": note},
        content=data,
        headers={**h, "Content-Type": ctype},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _compare(client, h, doc_id, frm, to=None, status=200):
    params = {"from": frm, **({"to": to} if to else {})}
    r = client.get(f"{API}/docs/{doc_id}/revisions/compare", params=params, headers=h)
    assert r.status_code == status, r.text
    return r.json()


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Quote"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    w = PdfWriter()
    page = w.add_blank_page(width=200, height=200)
    # pypdf cannot typeset text; a page annotation is extracted by `extract_text`? No: use a
    # text object via a content stream so extraction finds it.
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_workbook_compare_keeps_actual_shape():
    from slate.service.doc_compare import _xlsx_sheets

    assert _xlsx_sheets(_xlsx([["Only cell"]])) == [("Quote", [["Only cell"]])]


def test_formula_changes_are_compared_without_cached_values(client, alex):
    doc = _file_doc(client, alex, title="Formula")
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    for formula in ("=1+1", "=2+2"):
        _upload(client, alex, doc["id"], _xlsx([[formula]]), "b.xlsx", formula, ct)
    out = _compare(client, alex, doc["id"], 1)
    assert out["sheets"][0]["changed"] == [{"row": 0, "col": 0, "before": "=1+1", "after": "=2+2"}]


def _docx(xml):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_docx_text_is_searchable_and_compared(client, alex):
    doc = _file_doc(client, alex, title="Word")
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    for text in ("Order chairs", "Order tables"):
        data = _docx(
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
        )
        _upload(client, alex, doc["id"], data, "scope.docx", text, ct)
    hits = client.get(f"{API}/docs", params={"q": "tables"}, headers=alex).json()
    assert [h["id"] for h in hits] == [doc["id"]]
    diff = _compare(client, alex, doc["id"], 1)
    assert "+Order tables" in diff["diff"] and diff["approximate"]


def test_unreadable_word_compare_does_not_claim_identical_text(client, alex):
    doc = _file_doc(client, alex, title="Broken Word")
    for data in (b"broken one", b"broken two"):
        _upload(client, alex, doc["id"], data, "bad.docx", "broken", "application/octet-stream")
    assert _compare(client, alex, doc["id"], 1)["kind"] == "none"


def test_docx_extraction_rejects_entities_and_oversize(monkeypatch):
    from slate.service import doc_compare
    from slate.settings import settings

    monkeypatch.setattr(settings, "doc_extract_max_bytes", 200)
    assert doc_compare.extract_text("bad.docx", "", _docx(b"x" * 201)) is None
    assert (
        doc_compare.extract_text(
            "bad.docx", "", _docx(b'<!DOCTYPE x [<!ENTITY x "evil">]><x>&x;</x>')
        )
        is None
    )


def test_text_files_diff_line_by_line(client, alex):
    d = _file_doc(client, alex, title="Schedule")
    _upload(client, alex, d["id"], b"Doors 6 pm\nDinner 7 pm\n", "notes.txt", "first", "text/plain")
    _upload(
        client,
        alex,
        d["id"],
        b"Doors 6 pm\nDinner 8 pm\nMusic 9 pm\n",
        "notes.txt",
        "longer",
        "text/plain",
    )
    out = _compare(client, alex, d["id"], 1)
    assert out["kind"] == "text" and out["from_revision"] == 1 and out["to_revision"] == 2
    assert out["added"] == 2 and out["removed"] == 1 and "+Dinner 8 pm" in out["diff"]
    assert out["approximate"] is False
    # Explicit `to`, and the same revision twice is an empty diff.
    assert _compare(client, alex, d["id"], 1, 2)["diff"] == out["diff"]
    assert _compare(client, alex, d["id"], 2, 2)["diff"] == ""
    _compare(client, alex, d["id"], 9, status=404)
    page = client.post(f"{API}/docs", json={"title": "P"}, headers=alex).json()
    _compare(client, alex, page["id"], 1, status=422)


def test_csv_and_xlsx_diff_cell_by_cell(client, alex):
    d = _file_doc(client, alex, title="Quote")
    _upload(
        client, alex, d["id"], b"item,price\ntickets,120\nposters,2\n", "q.csv", "first", "text/csv"
    )
    _upload(
        client,
        alex,
        d["id"],
        b"item,price\ntickets,135\nposters,2\nflyers,1\n",
        "q.csv",
        "price up",
        "text/csv",
    )
    out = _compare(client, alex, d["id"], 1)
    assert out["kind"] == "table"
    sheet = out["sheets"][0]
    assert sheet["name"] == "q.csv"
    assert sheet["changed"] == [{"row": 1, "col": 1, "before": "120", "after": "135"}]
    assert sheet["added_rows"] == [3] and sheet["removed_rows"] == []
    assert sheet["rows"][3] == ["flyers", "1"] and sheet["before_rows"][1] == ["tickets", "120"]

    x = _file_doc(client, alex, title="Book")
    xt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    _upload(client, alex, x["id"], _xlsx([["a", "b"], [1, 2]]), "b.xlsx", "first", xt)
    _upload(client, alex, x["id"], _xlsx([["a", "b"], [1, 3]]), "b.xlsx", "b up", xt)
    out = _compare(client, alex, x["id"], 1)
    assert out["kind"] == "table" and out["sheets"][0]["name"] == "Quote"
    assert out["sheets"][0]["changed"] == [{"row": 1, "col": 1, "before": "2", "after": "3"}]
    # The sheet's text is searchable.
    hits = client.get(f"{API}/docs", params={"q": "tickets"}, headers=alex).json()
    assert [h["id"] for h in hits] == [d["id"]] and "tickets" in hits[0]["snippet"]


def test_pdf_text_diff_is_labelled_approximate(client, alex):
    d = _file_doc(client, alex, title="Venue")
    _upload(client, alex, d["id"], _pdf("Room holds 300"), "venue.pdf", "first", "application/pdf")
    _upload(client, alex, d["id"], _pdf("Room holds 320"), "venue.pdf", "more", "application/pdf")
    out = _compare(client, alex, d["id"], 1)
    assert out["kind"] == "text" and out["approximate"] is True
    assert "-Room holds 300" in out["diff"] and "+Room holds 320" in out["diff"]
    hits = client.get(f"{API}/docs", params={"q": "holds 320"}, headers=alex).json()
    assert [h["id"] for h in hits] == [d["id"]]
    rev = client.get(f"{API}/docs/{d['id']}/revisions", headers=alex).json()[0]
    assert rev["has_text"] is True


def test_images_and_unknown_types_have_no_server_diff(client, alex):
    d = _file_doc(client, alex, title="Photo")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
    _upload(client, alex, d["id"], png, "a.png", "first", "image/png")
    _upload(client, alex, d["id"], png + b"1", "a.png", "second", "image/png")
    out = _compare(client, alex, d["id"], 1)
    assert out["kind"] == "none" and out["from"]["url"] and out["to"]["url"]
    z = _file_doc(client, alex, title="Zip")
    _upload(client, alex, z["id"], b"PK\x03\x04", "a.zip", "first", "application/zip")
    _upload(client, alex, z["id"], b"PK\x03\x04\x00", "a.zip", "second", "application/zip")
    assert _compare(client, alex, z["id"], 1)["kind"] == "none"
    rev = client.get(f"{API}/docs/{z['id']}/revisions", headers=alex).json()[0]
    assert rev["has_text"] is False


def test_compare_reads_stored_tables_and_never_parses_on_read(client, alex, monkeypatch):
    """Compare answers from the tables stored at upload; it never re-extracts a revision."""
    from slate.service import doc_compare

    x = _file_doc(client, alex, title="Book")
    xt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    _upload(client, alex, x["id"], _xlsx([["a", "b"], [1, 2]]), "b.xlsx", "first", xt)
    _upload(client, alex, x["id"], _xlsx([["a", "b"], [1, 3]]), "b.xlsx", "b up", xt)
    d = _file_doc(client, alex, title="Quote")
    _upload(client, alex, d["id"], b"a,b\n1,2\n", "q.csv", "first", "text/csv")
    _upload(client, alex, d["id"], b"a,b\n1,3\n", "q.csv", "second", "text/csv")

    def never(*_a, **_k):
        raise AssertionError("compare must read what upload stored, not parse the file")

    for name in ("_xlsx_sheets", "_csv_rows", "_pdf_text", "_docx_text", "tables_of", "extract"):
        monkeypatch.setattr(doc_compare, name, never)
    for doc in (x, d):
        out = _compare(client, alex, doc["id"], 1)
        assert out["kind"] == "table" and out["message"] is None
        assert out["sheets"][0]["changed"] == [{"row": 1, "col": 1, "before": "2", "after": "3"}]


def test_a_revision_without_stored_tables_compares_as_not_indexed(client, alex, db):
    from conftest import raw_write
    from slate.models import DocFile
    from slate.service import doc_compare

    d = _file_doc(client, alex, title="Quote")
    old = _upload(client, alex, d["id"], b"a,b\n1,2\n", "q.csv", "first", "text/csv")
    _upload(client, alex, d["id"], b"a,b\n1,3\n", "q.csv", "second", "text/csv")
    # As a revision uploaded before the column existed (raw SQL: the ORM guard refuses it).
    raw_write("UPDATE doc_files SET tables_json = NULL WHERE id = :id", {"id": old["id"]})
    out = _compare(client, alex, d["id"], 1)
    assert out["kind"] == "none" and out["sheets"] == []
    assert "revision 1 is not indexed" in out["message"]
    assert "backfill-doc-text" in out["message"]
    # The backfill fills the tables without touching the text it already had.
    db.expire_all()
    assert doc_compare.backfill_text(db) == 1
    db.expire_all()
    row = db.get(DocFile, old["id"])
    assert row.tables_json == [["q.csv", [["a", "b"], ["1", "2"]]]] and row.text == "a,b\n1,2\n"
    assert doc_compare.backfill_text(db) == 0
    assert _compare(client, alex, d["id"], 1)["kind"] == "table"


def test_an_upload_over_the_extraction_cap_is_stored_viewable_and_unindexed(
    client, alex, monkeypatch
):
    from slate.settings import settings

    xt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    big = _xlsx([["item", "price"]] + [[f"p{i}", i] for i in range(400)])
    monkeypatch.setattr(settings, "doc_extract_max_bytes", 2048)
    d = _file_doc(client, alex, title="Big")
    rev = _upload(client, alex, d["id"], big, "big.xlsx", "first", xt)
    assert rev["has_text"] is False  # Alex's upload never fails; it is only not searchable
    got = client.get(rev["url"], headers=alex)
    assert got.status_code == 200 and got.content == big
    _upload(client, alex, d["id"], big, "big.xlsx", "again", xt)
    out = _compare(client, alex, d["id"], 1)
    assert out["kind"] == "none" and "not indexed" in out["message"]
    assert client.get(f"{API}/docs", params={"q": "p399"}, headers=alex).json() == []
    monkeypatch.setattr(settings, "doc_extract_max_bytes", 64 * 1024 * 1024)
    assert _upload(client, alex, d["id"], big, "big.xlsx", "indexed", xt)["has_text"] is True
