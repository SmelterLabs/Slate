"""Compare two revisions of a file doc by what the type allows, and extract a revision's
text once on upload so search finds it.

- Text-like files (`.txt .md .csv .json .gcode` and anything `text/*`) diff line by line.
- CSV, TSV and XLSX diff cell by cell: changed cells, added and removed rows, per sheet, with
  both tables so the board can paint them.
- PDFs diff the text pypdf extracts from each revision: approximate (layout is lost, a changed
  dimension or price still shows), and the answer says so.
- Images, archives, CAD and the rest have no server diff (`kind: none`); the board shows the two
  revisions side by side or with a swipe.

Slate never edits a file: a spreadsheet is edited in Excel and uploaded as the next revision;
this module shows what changed between the two.
"""

from __future__ import annotations

import csv
import difflib
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from slate.models import DocFile
from slate.service import docs as docs_svc
from slate.service.core import apply_change
from slate.service.errors import Invalid
from slate.settings import settings

log = logging.getLogger(__name__)

MAX_TEXT = 300_000  # characters kept per revision
MAX_TABLE_ROWS = 2_000
MAX_TABLE_COLS = 200
MAX_TABLE_SHEETS = 64
# A zip member this much bigger than its compressed bytes is a bomb, not a spreadsheet: real
# OOXML parts deflate 10-50x. Only members over RATIO_FLOOR count (a tiny part may be anything).
MAX_INFLATE_RATIO = 200
RATIO_FLOOR = 4 * 1024 * 1024
NOT_INDEXED = (
    "revision {no} is not indexed (its text was not extracted: larger than the server reads "
    "for search, unreadable, or uploaded before indexing existed; `slate backfill-doc-text` "
    "indexes older uploads), so there is no server diff: compare by eye"
)


def _cap() -> int:
    """Inflated bytes the server reads out of one upload (`SLATE_DOC_EXTRACT_MAX_BYTES`)."""
    return settings.doc_extract_max_bytes


def _check_archive(data: bytes) -> None:
    """Refuse an OOXML package before any parser inflates it: every member's declared size and
    their total must fit the cap, no member may inflate absurdly, and the declared sizes are
    then verified by inflating each member in chunks under the same budget (a central directory
    can lie). Raises ValueError, the failure `extract` turns into "not indexed"."""
    cap = _cap()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        total = 0
        for info in archive.infolist():
            if info.file_size > cap:
                raise ValueError(f"{info.filename} inflates to {info.file_size} bytes, over cap")
            total += info.file_size
            if total > cap:
                raise ValueError(f"the package inflates to over {cap} bytes")
            if info.file_size > RATIO_FLOOR and info.file_size > MAX_INFLATE_RATIO * max(
                info.compress_size, 1
            ):
                raise ValueError(f"{info.filename} inflates {MAX_INFLATE_RATIO}x or more")
        seen = 0
        for info in archive.infolist():
            with archive.open(info) as source:
                while chunk := source.read(64 * 1024):
                    seen += len(chunk)
                    if seen > cap:
                        raise ValueError(f"the package inflates to over {cap} bytes")


_TEXT_EXT = re.compile(
    r"\.(txt|md|markdown|json|ya?ml|toml|xml|log|gcode|nc|ini|cfg|py|js|ts|sh|sql|html?|css|svg)$",
    re.IGNORECASE,
)
_TABLE_EXT = re.compile(r"\.(csv|tsv)$", re.IGNORECASE)
_XLSX_TYPES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
)


def text_kind(filename: str, content_type: str) -> str:
    """`text`, `table` (CSV/TSV), `xlsx`, `pdf` or `none`: how the bytes turn into text."""
    ct = (content_type or "").lower()
    name = filename or ""
    if ct == "application/pdf" or name.lower().endswith(".pdf"):
        return "pdf"
    if (
        ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or name.lower().endswith(".docx")
    ):
        return "docx"
    if ct in _XLSX_TYPES or name.lower().endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if ct in ("text/csv", "text/tab-separated-values") or _TABLE_EXT.search(name):
        return "table"
    if (
        ct.startswith("text/")
        or ct in ("application/json", "application/xml")
        or _TEXT_EXT.search(name)
    ):
        return "text"
    return "none"


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _csv_rows(text: str) -> list[list[str]]:
    sample = text[:4096]
    delim = "\t" if "\t" in sample and sample.count("\t") >= sample.count(",") else ","
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        rows.append([c for c in row[:MAX_TABLE_COLS]])
        if len(rows) >= MAX_TABLE_ROWS:
            break
    return rows


def _xlsx_sheets(data: bytes) -> list[tuple[str, list[list[str]]]]:
    # openpyxl loads xl/sharedStrings.xml whole even read-only; the row and column limits
    # below only apply after that, so the package is measured first.
    _check_archive(data)
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    out: list[tuple[str, list[list[str]]]] = []
    for ws in wb.worksheets[:MAX_TABLE_SHEETS]:
        rows: list[list[str]] = []
        for row in ws.iter_rows(
            values_only=True,
            max_row=min(ws.max_row or MAX_TABLE_ROWS, MAX_TABLE_ROWS),
            max_col=min(ws.max_column or MAX_TABLE_COLS, MAX_TABLE_COLS),
        ):
            cells = ["" if v is None else str(v) for v in row[:MAX_TABLE_COLS]]
            while cells and cells[-1] == "":
                cells.pop()
            rows.append(cells)
            if len(rows) >= MAX_TABLE_ROWS:
                break
        while rows and not rows[-1]:
            rows.pop()
        out.append((ws.title, rows))
    wb.close()
    return out


def _docx_text(data: bytes) -> str:
    # Streaming target: no XML tree, descendant rescans, or unbounded output join.
    # Continue parsing after the output cap to reject malformed/DTD-bearing tails too.
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    class TextTarget:
        def __init__(self):
            self.out = io.StringIO()
            self.remaining = MAX_TEXT
            self.depth = 0
            self.paragraph = False
            self.text = False
            self.seen_paragraph = False

        def append(self, value):
            kept = value[: self.remaining]
            self.out.write(kept)
            self.remaining -= len(kept)

        def start(self, tag, attrs):
            self.depth += 1
            if self.depth > 128:
                raise ValueError("Word XML nesting is too deep")
            if tag == ns + "p":
                if self.paragraph:
                    raise ValueError("Nested Word paragraphs are not supported")
                self.paragraph = True
                if self.seen_paragraph:
                    self.append("\n")
                self.seen_paragraph = True
            if tag == ns + "t":
                if self.text:
                    raise ValueError("Nested Word text is not supported")
                self.text = True

        def end(self, tag):
            if tag == ns + "t":
                self.text = False
            if tag == ns + "p":
                self.paragraph = False
            self.depth -= 1

        def data(self, value):
            if self.paragraph and self.text and self.remaining:
                self.append(value)

        def doctype(self, name, pubid, system):
            raise ValueError("DOCTYPE is not allowed in OOXML")

        def close(self):
            return self.out.getvalue()

    parser = ET.XMLParser(target=TextTarget())
    cap = _cap()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        part = archive.getinfo("word/document.xml")
        if part.file_size > cap:
            raise ValueError("Word text part is too large")
        with archive.open(part) as source:
            size = 0
            while chunk := source.read(64 * 1024):
                size += len(chunk)
                if size > cap:
                    raise ValueError("Word text part is too large")
                parser.feed(chunk)
    return parser.close()


def _streams_of(page: Any) -> list[Any]:
    """The content streams pypdf decodes to read a page's text: its `/Contents` (one stream
    or an array) and the form XObjects its resources draw."""
    found: list[Any] = []
    contents = page.get("/Contents")
    contents = contents.get_object() if contents is not None else None
    if isinstance(contents, list):
        found.extend(c.get_object() for c in contents)
    elif contents is not None:
        found.append(contents)
    resources = page.get("/Resources")
    xobjects = resources.get_object().get("/XObject") if resources is not None else None
    if xobjects is not None:
        for ref in xobjects.get_object().values():
            obj = ref.get_object()
            if obj.get("/Subtype") == "/Form":
                found.append(obj)
    return found


def _decoded_size(stream: Any, remaining: int) -> int:
    """How many bytes `stream` inflates to, measured with a zlib output cap so a stream over
    `remaining` costs at most that many bytes to find out (it then reads remaining + 1)."""
    raw = getattr(stream, "_data", b"") or b""
    filters = stream.get("/Filter")
    filters = filters.get_object() if filters is not None else None
    names = [str(f) for f in filters] if isinstance(filters, list) else [str(filters or "")]
    if names and names[0] == "/FlateDecode":
        return len(zlib.decompressobj().decompress(raw, remaining + 1))
    return len(raw)


class _OverBudget(Exception):
    pass


def _pdf_text(data: bytes) -> str:
    """Pages' text, within MAX_TEXT characters and the inflated-byte cap: pypdf inflates each
    page's FlateDecode content to read it, so pages past the budget are not opened."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    remaining = _cap()
    for n, page in enumerate(reader.pages, 1):
        try:
            for stream in _streams_of(page):
                remaining -= _decoded_size(stream, remaining)
                if remaining < 0:
                    raise _OverBudget()
            text = page.extract_text() or ""
        except _OverBudget:
            break
        except Exception:  # noqa: BLE001 - a broken page must not sink the upload
            text = ""
        pages.append(f"--- page {n} ---\n{text.strip()}")
        if sum(len(p) for p in pages) > MAX_TEXT:
            break
    return "\n".join(pages)


def tables_of(filename: str, content_type: str, data: bytes) -> list[tuple[str, list[list[str]]]]:
    """Named tables in a CSV/TSV (one, named after the file) or an XLSX (one per sheet). Upload
    stores this (`extract`); compare reads the stored copy and never calls it."""
    kind = text_kind(filename, content_type)
    if kind == "table":
        return [(filename, _csv_rows(_decode(data)))]
    if kind == "xlsx":
        return _xlsx_sheets(data)
    return []


Tables = list[list[Any]]  # [[sheet name, rows], ...]: the JSON in `doc_files.tables_json`


def extract(filename: str, content_type: str, data: bytes) -> tuple[str | None, Tables | None]:
    """What a revision keeps for search and compare, read once on upload: its searchable text
    (None for a type that has none) and, for a CSV/TSV/XLSX, its tables as JSON so compare
    never parses the file again. Never raises: a file that cannot be read, or one over the
    `SLATE_DOC_EXTRACT_MAX_BYTES` inflated-byte cap, is uploaded all the same, unindexed."""
    kind = text_kind(filename, content_type)
    tables: Tables | None = None
    try:
        if kind == "text":
            text = _decode(data)
        elif kind == "table":
            text = _decode(data)
            tables = [[filename, _csv_rows(text)]]
        elif kind == "xlsx":
            sheets = _xlsx_sheets(data)
            tables = [[name, rows] for name, rows in sheets]
            text = "\n\n".join(
                f"## {name}\n" + "\n".join("\t".join(r) for r in rows) for name, rows in sheets
            )
        elif kind == "docx":
            text = _docx_text(data)
        elif kind == "pdf":
            text = _pdf_text(data)
        else:
            return None, None
    except Exception as e:  # noqa: BLE001
        log.info("no text from %s (%s): %s", filename, content_type, e)
        return None, None
    text = text.replace("\x00", "")
    return (text[:MAX_TEXT] if text.strip() else None), tables


def extract_text(filename: str, content_type: str, data: bytes) -> str | None:
    """The searchable text of a revision (see `extract`)."""
    return extract(filename, content_type, data)[0]


def _unified(a: str, b: str, from_no: int, to_no: int) -> tuple[str, int, int]:
    lines = list(
        difflib.unified_diff(
            a.splitlines(),
            b.splitlines(),
            fromfile=f"rev{from_no}",
            tofile=f"rev{to_no}",
            lineterm="",
        )
    )
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return "\n".join(lines), added, removed


def _table_diff(name: str, before: list[list[str]], after: list[list[str]]) -> dict[str, Any]:
    """Rows aligned by index (a spreadsheet re-saved keeps its shape); changed cells, rows only
    in one side. Good enough for a quote that changed a price; not a merge algorithm."""
    changed = []
    common = min(len(before), len(after))
    for r in range(common):
        b_row, a_row = before[r], after[r]
        for c in range(max(len(b_row), len(a_row))):
            bv = b_row[c] if c < len(b_row) else ""
            av = a_row[c] if c < len(a_row) else ""
            if bv != av:
                changed.append({"row": r, "col": c, "before": bv, "after": av})
    return {
        "name": name,
        "changed": changed,
        "added_rows": list(range(common, len(after))),
        "removed_rows": list(range(common, len(before))),
        "rows": after,
        "before_rows": before,
    }


def compare(
    session: Session, doc_id: str, from_no: int, to_no: int | None = None
) -> dict[str, Any]:
    """What changed between two revisions (`to` default: the current one)."""
    doc = docs_svc.get_doc(session, doc_id)
    if doc.kind != "file":
        raise Invalid("this doc is a page; use GET /docs/{id}/diff for page versions")
    old = docs_svc.get_revision(session, doc_id, from_no)
    if to_no is None:
        latest = docs_svc.revisions_of(session, doc_id)
        new = session.get(DocFile, latest[0].id) if latest else old
    else:
        new = docs_svc.get_revision(session, doc_id, to_no)
    assert new is not None
    out: dict[str, Any] = {
        "doc_id": doc.id,
        "from_revision": old.revision_no,
        "to_revision": new.revision_no,
        "from": docs_svc.revision_out(old),
        "to": docs_svc.revision_out(new),
        "kind": "none",
        "approximate": False,
        "diff": "",
        "added": 0,
        "removed": 0,
        "sheets": [],
        "message": None,
    }
    kind_old = text_kind(old.filename, old.content_type)
    kind_new = text_kind(new.filename, new.content_type)
    # Only what upload stored is read here: never the bytes, never a parser (re-extracting
    # two large workbooks on every GET takes seconds).
    if kind_old in ("table", "xlsx") and kind_new in ("table", "xlsx"):
        for rev in (old, new):
            if rev.tables_json is None:
                out["message"] = NOT_INDEXED.format(no=rev.revision_no)
                return out
        before = [(str(n), r) for n, r in old.tables_json]
        after = [(str(n), r) for n, r in new.tables_json]
        names = [n for n, _ in after] + [n for n, _ in before if n not in {x for x, _ in after}]
        b_by = dict(before)
        a_by = dict(after)
        sheets = []
        for name in names:
            sheets.append(_table_diff(name, b_by.get(name, []), a_by.get(name, [])))
        if len(before) == 1 and len(after) == 1 and before[0][0] != after[0][0]:
            # A CSV renamed between uploads is still one table.
            sheets = [_table_diff(after[0][0], before[0][1], after[0][1])]
        out.update(kind="table", sheets=sheets)
        out["added"] = sum(len(s["changed"]) + len(s["added_rows"]) for s in sheets)
        out["removed"] = sum(len(s["removed_rows"]) for s in sheets)
        return out
    if kind_old != "none" and kind_new != "none":
        for rev in (old, new):
            if rev.text is None:
                # Unknown text is not evidence of identical files.
                out["message"] = NOT_INDEXED.format(no=rev.revision_no)
                return out
        diff, added, removed = _unified(
            old.text, new.text, old.revision_no or 0, new.revision_no or 0
        )
        out.update(
            kind="text",
            approximate=(kind_old in ("pdf", "docx") or kind_new in ("pdf", "docx")),
            diff=diff,
            added=added,
            removed=removed,
        )
    return out


def _needs_index(f: DocFile) -> bool:
    kind = text_kind(f.filename, f.content_type)
    if kind == "none":
        return False
    return f.text is None or (kind in ("table", "xlsx") and f.tables_json is None)


def backfill_text(session: Session, *, limit: int = 1000) -> int:
    """Index revisions that were stored without an index: text for those with `text` NULL,
    tables for CSV/XLSX with `tables_json` NULL, as the system actor, one
    `doc.text_extracted` event per revision. Returns how many. A revision over the extraction
    cap stays unindexed and is skipped."""
    from slate.service.due import system_actor

    rows = list(
        session.scalars(
            select(DocFile)
            .where(
                DocFile.revision_no.is_not(None),
                or_(DocFile.text.is_(None), DocFile.tables_json.is_(None)),
            )
            .order_by(DocFile.created_at)
            .limit(limit)
        )
    )
    todo = [f for f in rows if _needs_index(f)]
    if not todo:
        return 0
    actor = system_actor(session)
    done = 0
    for f in todo:
        text, tables = extract(f.filename, f.content_type, f.data)
        if text is None and tables is None:
            continue
        with apply_change(session, actor) as cx:
            f.text = text
            f.tables_json = tables
            cx.record(
                "doc.text_extracted",
                subject_id=f.doc_id,
                after={
                    "revision_no": f.revision_no,
                    "chars": len(text or ""),
                    "tables": tables is not None,
                },
                covers=[f],
            )
        done += 1
    return done
