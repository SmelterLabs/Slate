"""Files in docs (part of `slate.service.docs`): images and files uploaded into a page, and the
revisions of a file doc.

A file doc (`kind = file`) is an uploaded PDF, spreadsheet or photo as a doc in the same tree as
pages. Its `doc_files` rows with a `revision_no` are its revisions, 1 upward: `add_revision`
adds the next one (a note saying what changed is required) and stamps the previous one
superseded; `restore_revision` copies an old one as the next; nothing is ever deleted. A row
without a `revision_no` is a file uploaded into a page (`add_file`), removable.

Writes: `doc.file_added`, `doc.file_removed`, `doc.revision_added`, `doc.revision_pinned`,
`doc.revision_unpinned`. Subject = the doc.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from slate.ids import new_id
from slate.models import Doc, DocFile, utcnow
from slate.service import attachments as attachments_svc
from slate.service.core import Actor, apply_change
from slate.service.doc_tree import get_doc
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


def _file_rows(session: Session, *criteria: Any, order_by: Any) -> list[DocFile]:
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
    rows = _file_rows(
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
        # Rows from `_file_rows` know it without the text; a row built here has it in hand.
        "has_text": f.has_text if hasattr(f, "has_text") else f.text is not None,
        "url": f"/api/v1/doc-files/{f.id}",
    }


def revisions_of(session: Session, doc_id: str) -> list[DocFile]:
    """A file doc's revisions, newest first, without bytes."""
    get_doc(session, doc_id)
    return _file_rows(
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
        get_doc(session, doc_id)
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
    if get_doc(session, doc_id).kind == "folder":
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


def _revision_row(
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
    from slate.service.doc_compare import extract

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
    doc = get_doc(session, doc_id)
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
    # The row is built through the docs module, so a caller that patches
    # `slate.service.docs._revision_row` (a race test) is honoured.
    from slate.service import docs as docs_svc

    rev = docs_svc._revision_row(
        actor, doc.id, filename, data, content_type, 0, text, restored_from
    )
    _store_revision(session, actor, doc, rev)
    return rev


def _store_revision(session: Session, actor: Actor, doc: Doc, rev: DocFile) -> None:
    """Add `rev` (built by `_revision_row`, outside the lock) as the doc's next revision."""
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
