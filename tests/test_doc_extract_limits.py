"""What one upload may cost the server to read: extraction stays inside fixed budgets of
characters, pages and time whatever the file claims to hold."""

from __future__ import annotations

import io
import json
import zipfile
import zlib

import pytest

from slate.service import doc_compare

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006"
XLSX = doc_compare._XLSX_TYPES[0]


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _shared_string_book(rows: int, cols: int, strlen: int) -> bytes:
    """A workbook whose every cell points at one long shared string: tiny on disk, huge if
    each cell is copied out in full."""
    buf = io.BytesIO()
    main = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            f'<?xml version="1.0"?><Types xmlns="{PKG}/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" '
            f'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{main}.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            f'ContentType="{main}.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" '
            f'ContentType="{main}.sharedStrings+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{PKG}/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0"?><workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>'
            '<sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{PKG}/relationships">'
            f'<Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/sharedStrings.xml",
            f'<?xml version="1.0"?><sst xmlns="{NS}" count="1" uniqueCount="1">'
            f"<si><t>{'A' * strlen}</t></si></sst>",
        )
        body = "".join(
            f'<row r="{r + 1}">'
            + "".join(f'<c r="{_col(c)}{r + 1}" t="s"><v>0</v></c>' for c in range(cols))
            + "</row>"
            for r in range(rows)
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<?xml version="1.0"?><worksheet xmlns="{NS}"><sheetData>{body}</sheetData>'
            "</worksheet>",
        )
    return buf.getvalue()


def _cells(tables):
    return [cell for _name, rows in tables for row in rows for cell in row]


# --- tables: one character budget for cells, sheets and text -------------------------------------


def test_shared_strings_cannot_amplify_a_small_workbook():
    data = _shared_string_book(rows=2000, cols=10, strlen=32_000)
    assert len(data) < 200_000  # a small upload ...
    text, tables = doc_compare.extract("a.xlsx", XLSX, data)
    cells = _cells(tables)
    # ... stays small once read: no cell is copied whole, and the total is capped.
    assert cells and max(len(c) for c in cells) <= doc_compare.MAX_CELL
    assert sum(len(c) for c in cells) <= doc_compare.MAX_TABLE_CHARS
    assert len(json.dumps(tables)) < 2 * doc_compare.MAX_TABLE_CHARS
    assert text is not None and len(text) <= doc_compare.MAX_TEXT


def test_csv_cells_share_the_same_budget():
    row = ",".join(["B" * 5000] * 50)
    data = "\n".join([row] * 100).encode()
    text, tables = doc_compare.extract("a.csv", "text/csv", data)
    cells = _cells(tables)
    assert max(len(c) for c in cells) <= doc_compare.MAX_CELL
    assert sum(len(c) for c in cells) <= doc_compare.MAX_TABLE_CHARS
    assert text is not None and len(text) <= doc_compare.MAX_TEXT


def _pdf(pages: int, content: bytes, filt: str, *, text: bytes = b"") -> bytes:
    """A minimal PDF whose `pages` pages all draw one content stream object, encoded with
    `filt` (`/FlateDecode`, `/Fl`, the chain `AHx+Fl`, or `none`)."""
    if filt in ("/FlateDecode", "/Fl"):
        stream, decl = zlib.compress(content, 9), f" /Filter {filt}"
    elif filt == "AHx+Fl":
        stream = zlib.compress(content, 9).hex().encode() + b">"
        decl = " /Filter [/ASCIIHexDecode /FlateDecode]"
    else:
        stream, decl = content, ""
    font = (
        b" /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>"
    )
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{4 + i} 0 R' for i in range(pages))}] "
        f"/Count {pages} >>".encode(),
        f"<< /Length {len(stream)}{decl} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    objs += [
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R" + font + b" >>"
    ] * pages
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return out.getvalue()


HEAVY = b"0 0 m 1 1 l S\n" * (1024 * 1024 // 14)  # ~1 MiB of path operators, no text


def _pdf_cost(monkeypatch, data: bytes, cap: int) -> tuple[str | None, list[int]]:
    """Extract `data` under an inflate cap of `cap` bytes; return the text and the page
    numbers pypdf was asked to read."""
    from pypdf import PageObject

    read: list[int] = []
    real = PageObject.extract_text

    def counting(page, *a, **k):
        read.append(1)
        return real(page, *a, **k)

    monkeypatch.setattr(PageObject, "extract_text", counting)
    monkeypatch.setattr(doc_compare, "_cap", lambda: cap)
    text, _tables = doc_compare.extract("a.pdf", "application/pdf", data)
    return text, read


@pytest.mark.parametrize("filt", ["/FlateDecode", "/Fl", "AHx+Fl"])
def test_every_spelling_of_flate_counts_against_the_inflate_budget(monkeypatch, filt):
    # 16 pages x 1 MiB of drawing under a 4 MiB budget: only the first 4 pages are read,
    # whatever the stream's filter is called.
    _text, read = _pdf_cost(monkeypatch, _pdf(16, HEAVY, filt), 4 * 1024 * 1024)
    assert len(read) <= 4


def test_a_stream_that_does_not_inflate_is_skipped_not_read(monkeypatch):
    data = _pdf(1, b"", "none").replace(b"<< /Length 0", b"<< /Length 5 /Filter /Fl", 1)
    data = data.replace(b"stream\n\nendstream", b"stream\nxxxxx\nendstream", 1)
    text, read = _pdf_cost(monkeypatch, data, 4 * 1024 * 1024)
    assert read == []
    assert text is not None


def test_pdf_reading_stops_at_the_page_limit(monkeypatch):
    monkeypatch.setattr(doc_compare, "MAX_PDF_PAGES", 5)
    _text, read = _pdf_cost(monkeypatch, _pdf(12, b"BT /F1 9 Tf 9 9 Td (hi) Tj ET", "none"), 10**9)
    assert len(read) == 5


def test_pdf_reading_stops_at_the_time_limit(monkeypatch):
    monkeypatch.setattr(doc_compare, "PDF_SECONDS", 0.0)
    _text, read = _pdf_cost(monkeypatch, _pdf(12, b"BT /F1 9 Tf 9 9 Td (hi) Tj ET", "none"), 10**9)
    assert len(read) <= 1


def test_nested_forms_count_against_the_budget():
    from pypdf.generic import DictionaryObject, NameObject

    inner = DictionaryObject({NameObject("/Subtype"): NameObject("/Form")})
    outer = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/Resources"): DictionaryObject(
                {NameObject("/XObject"): DictionaryObject({NameObject("/I"): inner})}
            ),
        }
    )
    page = DictionaryObject(
        {
            NameObject("/Resources"): DictionaryObject(
                {NameObject("/XObject"): DictionaryObject({NameObject("/O"): outer})}
            )
        }
    )
    found = doc_compare._streams_of(page)
    assert any(f is inner for f in found) and any(f is outer for f in found)


def test_a_readable_pdf_still_yields_its_text():
    text, _ = doc_compare.extract(
        "a.pdf",
        "application/pdf",
        _pdf(2, b"BT /F1 12 Tf 20 100 Td (Quarterly total) Tj ET", "/Fl"),
    )
    assert text is not None and text.count("Quarterly total") == 2


def test_ordinary_tables_are_kept_whole():
    text, tables = doc_compare.extract("q.csv", "text/csv", b"item,price\nbolt,0.25\n")
    assert tables == [["q.csv", [["item", "price"], ["bolt", "0.25"]]]]
    assert "bolt" in text
