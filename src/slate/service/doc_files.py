"""Document file uploads, revision history and bounded text extraction."""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from slate.ids import new_id
from slate.models import Doc, DocFile, Item, utcnow
from slate.service import attachments as attachments_svc
from slate.service import doc_pages
from slate.service.core import Actor, apply_change
from slate.service.errors import Invalid, NotFound
from slate.settings import settings

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REVISION_BYTES = 25 * 1024 * 1024  # a file doc's revision, like a task attachment

_FILE_COLUMNS = (
    DocFile.id,
    DocFile.doc_id,
    DocFile.filename,
    DocFile.content_type,
    DocFile.size,
    DocFile.uploaded_by,
    DocFile.created_at,
    DocFile.revision_no,
    DocFile.note,
    DocFile.superseded_at,
    DocFile.restored_from,
)


# --- reads -----------------------------------------------------------------------------------


def file_rows(session: Session, *criteria: Any, order_by: Any) -> list[DocFile]:
    """`doc_files` rows without bytes or extracted text. Each carries `has_text` (whether its
    text was extracted), read as a NULL test so a listing never loads the text itself (up to
    MAX_TEXT characters per revision); `revision_out` reports it."""
    rows = session.execute(
        select(DocFile, DocFile.text.is_not(None))
        .options(load_only(*_FILE_COLUMNS))
        .where(*criteria)
        .order_by(order_by)
    ).all()
    for f, has_text in rows:
        f.has_text = bool(has_text)  # type: ignore[attr-defined]
    return [f for f, _ in rows]


def current_revisions(session: Session, docs: Sequence[Doc]) -> dict[str, DocFile]:
    """The newest revision of each file doc among `docs`, by doc id (one query). Each row also
    carries `pinned_file_id`, the id of the doc's pinned revision when it has one."""
    ids = [d.id for d in docs if d.kind == "file"]
    if not ids:
        return {}
    pins = {d.id: d.pinned_revision_no for d in docs if d.pinned_revision_no is not None}
    rows = file_rows(
        session,
        DocFile.doc_id.in_(ids),
        DocFile.revision_no.is_not(None),
        order_by=DocFile.revision_no,
    )
    pinned_ids = {
        (f.doc_id, f.revision_no): f.id for f in rows if pins.get(f.doc_id) == f.revision_no
    }
    out: dict[str, DocFile] = {}
    for f in rows:  # ascending order: the last one per doc wins
        out[f.doc_id] = f
    for doc_id, f in out.items():
        f.pinned_file_id = pinned_ids.get((doc_id, pins.get(doc_id)))  # type: ignore[attr-defined]
    return out


def revision_out(f: DocFile) -> dict[str, Any]:
    """One revision of a file doc (metadata, never bytes)."""
    return {
        "id": f.id,
        "doc_id": f.doc_id,
        "revision_no": f.revision_no,
        "filename": f.filename,
        "content_type": f.content_type,
        "size": f.size,
        "note": f.note,
        "uploaded_by": f.uploaded_by,
        "created_at": f.created_at,
        "superseded_at": f.superseded_at,
        "restored_from": f.restored_from,
        # Rows from `file_rows` know it without the text; a row built here has it in hand.
        "has_text": f.has_text if hasattr(f, "has_text") else f.text is not None,
        "url": f"/api/v1/doc-files/{f.id}",
    }


def revisions_of(session: Session, doc_id: str) -> list[DocFile]:
    """A file doc's revisions, newest first, without bytes."""
    doc_pages.get_doc(session, doc_id)
    return file_rows(
        session,
        DocFile.doc_id == doc_id,
        DocFile.revision_no.is_not(None),
        order_by=DocFile.revision_no.desc(),
    )


def get_revision(session: Session, doc_id: str, revision_no: int) -> DocFile:
    f = session.scalar(
        select(DocFile).where(DocFile.doc_id == doc_id, DocFile.revision_no == revision_no)
    )
    if f is None:
        doc_pages.get_doc(session, doc_id)
        raise NotFound(f"doc {doc_id} has no revision {revision_no}")
    return f


def file_out(f: DocFile) -> dict[str, Any]:
    return {
        "id": f.id,
        "doc_id": f.doc_id,
        "filename": f.filename,
        "content_type": f.content_type,
        "size": f.size,
        "uploaded_by": f.uploaded_by,
        "created_at": f.created_at,
        "url": f"/api/v1/doc-files/{f.id}",
        "markdown": (
            f"![{f.filename}](/api/v1/doc-files/{f.id})"
            if f.content_type.startswith("image/")
            else f"[{f.filename}](/api/v1/doc-files/{f.id})"
        ),
    }


def get_file(session: Session, file_id: str) -> DocFile:
    f = session.get(DocFile, file_id)
    if f is None:
        raise NotFound(f"doc file {file_id} not found")
    return f


# --- the date in a file's name ----------------------------------------------------------------
# People name files by date ("2026-10-30 - Example Project.pdf", "Quote 10-30-26.xlsx"). A file doc
# whose name carries one is created *on that date*, so the drawer's Created column reads the
# way the folder does; the upload time is still on the revision. ISO first, then US order, then
# month names; a year is 2000-2099 (two digits mean 20xx). Noon workspace time, so the day
# never shifts.

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_DATE_PATTERNS = [
    # 2026-10-30, 2026_10_30, 2026.10.30, 20261030
    (re.compile(r"(?<!\d)(20\d\d)[-_. ]?(\d{1,2})[-_. ]?(\d{1,2})(?!\d)"), ("y", "m", "d")),
    # 10-30-2026, 10/30/26, 10.30.26
    (re.compile(r"(?<!\d)(\d{1,2})[-_./](\d{1,2})[-_./](20\d\d|\d\d)(?!\d)"), ("m", "d", "y")),
    # Oct 30 2026, October 30, 2026, 30 Oct 2026
    (
        re.compile(r"(?<![a-z])([a-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (20\d\d)(?!\d)", re.I),
        ("mn", "d", "y"),
    ),
    (re.compile(r"(?<!\d)(\d{1,2}) ([a-z]{3,9})\.?,? (20\d\d)(?!\d)", re.I), ("d", "mn", "y")),
]


def date_in_name(name: str) -> date | None:
    """The calendar date a filename or title carries, or None. The first match in the name
    wins; a match that is not a real date (month 13, Feb 30) is skipped."""
    for pattern, order in _DATE_PATTERNS:
        for m in pattern.finditer(name or ""):
            parts = dict(zip(order, m.groups(), strict=True))
            try:
                year = int(parts["y"])
                if year < 100:
                    year += 2000
                month = _MONTHS.get(parts["mn"].lower()) if "mn" in parts else int(parts["m"])
                if month is None:
                    continue
                return date(year, month, int(parts["d"]))
            except (ValueError, KeyError):
                continue
    return None


def created_from_name(name: str) -> datetime | None:
    """`date_in_name` as the stored timestamp: noon in the display timezone, in UTC."""
    d = date_in_name(name)
    if d is None:
        return None
    local = datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo(settings.timezone))
    return local.astimezone(UTC)


# --- files in a page --------------------------------------------------------------------------


def add_file(
    session: Session,
    actor: Actor,
    doc_id: str,
    *,
    filename: str,
    data: bytes,
    content_type: str | None = None,
) -> DocFile:
    """Upload an image or file into a doc; its `markdown` is ready to paste into the body."""
    if doc_pages.get_doc(session, doc_id).kind == "folder":
        raise Invalid("a folder holds documents, not files; put the file in a doc inside it")
    name, ctype = _clean_file(filename, data, content_type, MAX_FILE_BYTES)
    f = DocFile(
        id=new_id(),
        doc_id=doc_id,
        filename=name,
        content_type=ctype,
        size=len(data),
        data=data,
        uploaded_by=actor.account_id,
        created_at=utcnow(),
    )
    with apply_change(session, actor) as cx:
        cx.record("doc.file_added", subject_id=doc_id, after=file_out(f), covers=[f])
        session.add(f)
    return f


def remove_file(session: Session, actor: Actor, file_id: str) -> None:
    f = get_file(session, file_id)
    if f.revision_no is not None:
        raise Invalid("a revision is never removed; upload the next one or archive the doc")
    with apply_change(session, actor) as cx:
        cx.record("doc.file_removed", subject_id=f.doc_id, before=file_out(f), covers=[f])
        session.delete(f)


# --- file docs: revisions ---------------------------------------------------------------------


def _clean_file(
    filename: str, data: bytes, content_type: str | None, limit: int
) -> tuple[str, str]:
    """A doc file's stored name and type, by the rules every upload follows."""
    return attachments_svc.clean(filename, data, content_type, limit=limit, noun="file")


def revision_row(
    actor: Actor,
    doc_id: str,
    filename: str,
    data: bytes,
    content_type: str | None,
    revision_no: int,
    note: str,
    restored_from: int | None = None,
    extracted: tuple[str | None, Any] | None = None,
) -> DocFile:
    """A new revision row. `extracted` is the (text, tables) already stored for these same
    bytes; without it the file is read here, so call this before `apply_change`."""
    name, ctype = _clean_file(filename, data, content_type, MAX_REVISION_BYTES)
    text, tables = extracted if extracted is not None else extract(name, ctype, data)
    return DocFile(
        id=new_id(),
        doc_id=doc_id,
        filename=name,
        content_type=ctype,
        size=len(data),
        data=data,
        uploaded_by=actor.account_id,
        created_at=utcnow(),
        revision_no=revision_no,
        note=note,
        restored_from=restored_from,
        text=text,
        tables_json=tables,
    )


def _note(note: str | None) -> str:
    text = " ".join((note or "").split())[:200]
    if not text:
        raise Invalid("note is required: say what changed in this revision")
    return text


def _file_doc(session: Session, doc_id: str) -> Doc:
    doc = doc_pages.get_doc(session, doc_id)
    if doc.kind != "file":
        raise Invalid(f"this doc is a {doc.kind}; revisions belong to file docs (kind = file)")
    return doc


def pin_revision(
    session: Session, actor: Actor, doc_id: str, revision_no: int, note: str | None = None
) -> Doc:
    """Mark one revision as the one in use. Moving the pin to another
    revision is the same call. One `doc.revision_pinned`."""
    doc = _file_doc(session, doc_id)
    get_revision(session, doc_id, revision_no)
    text = " ".join((note or "").split())[:200] or None
    if doc.pinned_revision_no == revision_no and doc.pinned_note == text:
        return doc
    before = {"revision_no": doc.pinned_revision_no, "note": doc.pinned_note}
    with apply_change(session, actor) as cx:
        doc.pinned_revision_no = revision_no
        doc.pinned_note = text
        cx.record(
            "doc.revision_pinned",
            subject_id=doc.id,
            field="pinned_revision_no",
            before=before,
            after={"revision_no": revision_no, "note": text},
            covers=[doc],
        )
    return doc


def unpin_revision(session: Session, actor: Actor, doc_id: str) -> Doc:
    doc = _file_doc(session, doc_id)
    if doc.pinned_revision_no is None:
        return doc
    before = {"revision_no": doc.pinned_revision_no, "note": doc.pinned_note}
    with apply_change(session, actor) as cx:
        doc.pinned_revision_no = None
        doc.pinned_note = None
        cx.record(
            "doc.revision_unpinned",
            subject_id=doc.id,
            field="pinned_revision_no",
            before=before,
            after=None,
            covers=[doc],
        )
    return doc


def add_revision(
    session: Session,
    actor: Actor,
    doc_id: str,
    *,
    filename: str,
    data: bytes,
    note: str | None,
    content_type: str | None = None,
    restored_from: int | None = None,
) -> DocFile:
    """The next revision of a file doc. The previous one is stamped superseded and kept.

    The file is read before the write lock is taken; its number is chosen under the lock,
    from the history as it stands then, so two uploads at once get consecutive numbers."""
    doc = _file_doc(session, doc_id)
    text = _note(note)
    rev = revision_row(actor, doc.id, filename, data, content_type, 0, text, restored_from)
    _store_revision(session, actor, doc, rev)
    return rev


def _store_revision(session: Session, actor: Actor, doc: Doc, rev: DocFile) -> None:
    """Add `rev` (built by `revision_row`, outside the lock) as the doc's next revision."""
    now = rev.created_at
    with apply_change(session, actor) as cx:
        # Any row already dirty here is covered by an event, so this read may flush safely.
        latest = session.scalar(
            select(DocFile)
            .options(load_only(*_FILE_COLUMNS))
            .where(DocFile.doc_id == doc.id, DocFile.revision_no.is_not(None))
            .order_by(DocFile.revision_no.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        rev.revision_no = (latest.revision_no or 0) + 1 if latest else 1
        covers: list[Any] = [rev, doc]
        if latest is not None:
            latest.superseded_at = now
            covers.append(latest)
        doc.updated_by = actor.account_id
        doc.updated_at = now
        session.add(rev)
        cx.record("doc.revision_added", subject_id=doc.id, after=revision_out(rev), covers=covers)


def restore_revision(session: Session, actor: Actor, doc_id: str, revision_no: int) -> DocFile:
    """Make an older revision current again, as a new revision with its bytes. History stays."""
    _file_doc(session, doc_id)
    old = get_revision(session, doc_id, revision_no)
    old.data  # noqa: B018 - load the bytes before the change
    return add_revision(
        session,
        actor,
        doc_id,
        filename=old.filename,
        data=old.data,
        note=f"Restored revision {revision_no}",
        content_type=old.content_type,
        restored_from=revision_no,
    )


def promote_attachment(
    session: Session,
    actor: Actor,
    attachment_id: str,
    *,
    title: str | None = None,
    parent_id: str | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    note: str | None = None,
) -> Doc:
    """Move a task's attachment into the library: a new file doc with the bytes as revision 1,
    the attachment removed from the task, and a comment on the task saying where it went."""
    from slate.service.comments import add_comment

    att, data = attachments_svc.read_bytes(session, attachment_id)
    item = session.get(Item, att.item_id)
    text = _note(note or (f"From task {item.title}" if item else "From a task"))
    # The file is read before the write lock; the four steps are then one change, so a step
    # the caller may not take (removing the attachment needs edit on the task's list) leaves
    # nothing behind.
    rev = revision_row(actor, "", att.filename, data, att.content_type, 1, text)
    with apply_change(session, actor):
        doc = doc_pages.create_doc(
            session,
            actor,
            kind="file",
            title=title or att.filename,
            parent_id=parent_id,
            list_id=list_id,
            section_id=section_id if parent_id is None and list_id is None else None,
        )
        rev.doc_id = doc.id
        _store_revision(session, actor, doc, rev)
        attachments_svc.remove_attachment(session, actor, att.id)
        add_comment(
            session,
            actor,
            att.item_id,
            body_md=f"Moved **{att.filename}** to Docs & Files: [[doc:{doc.id}]]",
            keep_open=True,
        )
    return doc_pages.get_doc(session, doc.id)


# --- Search text and tables extracted from uploads ---


log = logging.getLogger(__name__)

MAX_TEXT = 300_000  # characters kept per revision
MAX_TABLE_ROWS = 2_000
MAX_TABLE_COLS = 200
MAX_TABLE_SHEETS = 64
# Characters kept per cell and in all of a revision's tables together. A workbook's cells can
# all point at one long shared string, so the inflated-size check alone does not bound what
# reading the cells out produces; this does. Past the budget the tables are cut short.
MAX_CELL = 1_000
MAX_TABLE_CHARS = 4_000_000
# A PDF is read for at most this many pages and seconds, and at most PDF_CONTENT_BYTES of
# decoded page content (pypdf parses about a megabyte a second in pure Python, and the
# deadline is only checked between pages), whatever its streams claim to weigh.
MAX_PDF_PAGES = 300
PDF_SECONDS = 15.0
PDF_CONTENT_BYTES = 16 * 1024 * 1024
MAX_FORM_DEPTH = 8
# A zip member this much bigger than its compressed bytes is a bomb, not a spreadsheet: real
# OOXML parts deflate 10-50x. Only members over RATIO_FLOOR count (a tiny part may be anything).
MAX_INFLATE_RATIO = 200
RATIO_FLOOR = 4 * 1024 * 1024


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


class _Budget:
    """Characters left for one revision's tables (`MAX_TABLE_CHARS`), shared by every sheet."""

    def __init__(self) -> None:
        self.left = MAX_TABLE_CHARS

    def cells(self, values: Any) -> list[str] | None:
        """One row's cells, each cut to `MAX_CELL`; None once the budget is spent."""
        if self.left <= 0:
            return None
        out = []
        for v in values:
            cell = "" if v is None else str(v)[:MAX_CELL]
            self.left -= len(cell)
            out.append(cell)
        return out


def _csv_rows(text: str) -> list[list[str]]:
    budget = _Budget()
    sample = text[:4096]
    delim = "\t" if "\t" in sample and sample.count("\t") >= sample.count(",") else ","
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        cells = budget.cells(row[:MAX_TABLE_COLS])
        if cells is None:
            break
        rows.append(cells)
        if len(rows) >= MAX_TABLE_ROWS:
            break
    return rows


def _xlsx_sheets(data: bytes) -> list[tuple[str, list[list[str]]]]:
    # openpyxl loads xl/sharedStrings.xml whole even read-only; the row and column limits
    # below only apply after that, so the package is measured first.
    _check_archive(data)
    import openpyxl

    budget = _Budget()
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    out: list[tuple[str, list[list[str]]]] = []
    for ws in wb.worksheets[:MAX_TABLE_SHEETS]:
        if budget.left <= 0:
            break
        rows: list[list[str]] = []
        for row in ws.iter_rows(
            values_only=True,
            max_row=min(ws.max_row or MAX_TABLE_ROWS, MAX_TABLE_ROWS),
            max_col=min(ws.max_column or MAX_TABLE_COLS, MAX_TABLE_COLS),
        ):
            cells = budget.cells(row[:MAX_TABLE_COLS])
            if cells is None:
                break
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


def _tables_text(sheets: list[tuple[str, list[list[str]]]]) -> str:
    """The sheets as searchable text, written up to `MAX_TEXT` characters and no further."""
    out = io.StringIO()
    left = MAX_TEXT
    for n, (name, rows) in enumerate(sheets):
        for part in ("\n\n" if n else "", f"## {name}\n", *_lines(rows)):
            if left <= 0:
                return out.getvalue()
            out.write(part[:left])
            left -= len(part)
    return out.getvalue()


def _lines(rows: list[list[str]]) -> Any:
    for n, r in enumerate(rows):
        yield ("\n" if n else "") + "\t".join(r)


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
    or an array) and every form XObject its resources draw, nested forms included (each
    object once, at most `MAX_FORM_DEPTH` levels down)."""
    found: list[Any] = []
    contents = page.get("/Contents")
    contents = contents.get_object() if contents is not None else None
    if isinstance(contents, list):
        found.extend(c.get_object() for c in contents)
    elif contents is not None:
        found.append(contents)
    seen: set[int] = set()

    def forms(holder: Any, depth: int) -> None:
        resources = holder.get("/Resources")
        resources = resources.get_object() if resources is not None else None
        xobjects = resources.get("/XObject") if resources is not None else None
        if xobjects is None:
            return
        if depth >= MAX_FORM_DEPTH:
            raise ValueError("form XObjects nest too deep")
        for ref in xobjects.get_object().values():
            obj = ref.get_object()
            if id(obj) in seen or obj.get("/Subtype") != "/Form":
                continue
            seen.add(id(obj))
            found.append(obj)
            forms(obj, depth + 1)

    forms(page, 0)
    return found


# pypdf accepts these abbreviations for a stream's filter (PDF 32000-1, table 94).
_FILTER_NAMES = {
    "/Fl": "/FlateDecode",
    "/AHx": "/ASCIIHexDecode",
    "/A85": "/ASCII85Decode",
    "/LZW": "/LZWDecode",
    "/RL": "/RunLengthDecode",
    "/CCF": "/CCITTFaxDecode",
    "/DCT": "/DCTDecode",
}
# Image codecs: pypdf does not decode them to read text, so their bytes cost what they weigh.
_IMAGE_FILTERS = {"/CCITTFaxDecode", "/DCTDecode", "/JBIG2Decode", "/JPXDecode"}


def _filters(stream: Any) -> list[str]:
    value = stream.get("/Filter")
    value = value.get_object() if value is not None else None
    if value is None:
        return []
    names = list(value) if isinstance(value, list) else [value]
    return [_FILTER_NAMES.get(str(n), str(n)) for n in names]


def _decoded_size(stream: Any, remaining: int) -> int:
    """How many bytes `stream` decodes to, through its whole filter chain with the names
    normalised as pypdf reads them. Flate output is capped at `remaining + 1`, so a stream
    over the budget costs at most that much to measure; the ASCII filters only shrink or
    expand their input a little. A filter that cannot be measured cheaply (LZW, run-length,
    an unknown name) or a stream that will not decode raises ValueError: the page is then
    not read."""
    data = getattr(stream, "_data", b"") or b""
    for name in _filters(stream):
        if name in _IMAGE_FILTERS:
            break
        if name == "/FlateDecode":
            try:
                data = zlib.decompressobj().decompress(data, remaining + 1)
            except zlib.error as e:
                raise ValueError(f"a content stream does not inflate: {e}") from None
        elif name == "/ASCIIHexDecode":
            from pypdf.filters import ASCIIHexDecode

            data = ASCIIHexDecode.decode(data)
        elif name == "/ASCII85Decode":
            from pypdf.filters import ASCII85Decode

            data = ASCII85Decode.decode(data)
        else:
            raise ValueError(f"content stream filter {name} is not measured")
        if len(data) > remaining:
            break
    return len(data)


class _OverBudget(Exception):
    pass


def _pdf_text(data: bytes) -> str:
    """Pages' text, within fixed limits on the work: `MAX_TEXT` characters, `MAX_PDF_PAGES`
    pages, `PDF_SECONDS` of reading, and the inflated-byte cap. pypdf decodes each page's
    content streams to read it, so they are measured first and pages past the budget are
    never opened; a page whose streams cannot be measured is skipped."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    chars = 0
    remaining = min(_cap(), PDF_CONTENT_BYTES)
    deadline = time.monotonic() + PDF_SECONDS
    for n, page in enumerate(reader.pages, 1):
        if n > MAX_PDF_PAGES or time.monotonic() >= deadline:
            break
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
        chars += len(pages[-1])
        if chars > MAX_TEXT:
            break
    return "\n".join(pages)


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
            text = _tables_text(sheets)
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
