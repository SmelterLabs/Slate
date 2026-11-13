"""/docs: ClickUp-style pages (nested, versioned, with task embeds) and /doc-files/{id}.

A page's body is Markdown. `[[task:<id>]]` on its own line is a live task card on the board;
`[[doc:<id>]]` links another page. Agents keep plans and notes here: `append_md` adds to the
end, `replace_section` rewrites one heading's part, and `base_revision` refuses an edit made
against text that has since changed (409) rather than overwriting it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from slate.api.attachments import CSP
from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.lists import list_out
from slate.api.schemas import ChildIn, ItemOut, ListOut
from slate.api.uploads import read_body
from slate.service import attachments as attachments_svc
from slate.service import docs

router = APIRouter(tags=["docs"])

Template = Literal["blank", "meeting", "plan", "decision", "runbook", "agent_log"]
Kind = Literal["page", "file", "folder"]


class DocIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200, description="Default: the template's")
    body_md: str | None = Field(default=None, description="Markdown; default: the template's")
    icon: str | None = Field(default=None, max_length=16, description="One emoji")
    parent_id: str | None = Field(default=None, description="Makes it a subpage of that doc")
    list_id: str | None = Field(default=None, description="A top page in this list")
    section_id: str | None = Field(
        default=None, description="A top page in this section (no list, no parent: the workspace)"
    )
    template: Template | None = Field(default=None, description="See GET /docs/templates")
    before_id: str | None = Field(default=None, description="Place it before this sibling")
    kind: Kind = Field(
        default="page",
        description="`file`: an uploaded file with revisions (POST /docs/{id}/revisions next), "
        "its body a description; `folder`: a container inside a drawer",
    )
    tags: list[str] | None = Field(default=None, description="Tag names; unknown ones are made")


class ReplaceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=1, max_length=200, description="Heading text, any level")
    body_md: str = Field(description="What goes under it, up to the next heading at its level")


class DocPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    icon: str | None = Field(default=None, max_length=16, description="One emoji; null clears")
    body_md: str | None = Field(default=None, description="Replaces the whole body")
    append_md: str | None = Field(default=None, description="Added as a paragraph at the end")
    replace_section: ReplaceSection | None = Field(
        default=None, description="Rewrite one heading's part (added at the end if missing)"
    )
    base_revision: int | None = Field(
        default=None, description="The revision you edited; 409 if the doc has changed since"
    )
    new_version: bool = Field(
        default=False, description="Close the current page-history version after this save"
    )
    note: str | None = Field(
        default=None, max_length=200, description="Name the version this save closes"
    )
    tags: list[str] | None = Field(default=None, description="Replaces the doc's tags (names)")


class DocMoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: str | None = Field(default=None, description="Under this page")
    list_id: str | None = Field(default=None, description="Or a top page in this list")
    section_id: str | None = Field(default=None, description="Or in this section")
    before_id: str | None = Field(default=None, description="Before this sibling; default last")


class DocDuplicateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    subpages: bool = Field(default=True, description="Copy the subpages too")


class DocTaskIn(ChildIn):
    list_id: str | None = Field(default=None, description="Default: the doc's list")
    replace: str | None = Field(
        default=None, description="Text in the doc that becomes the task's embed (a selection)"
    )
    occurrence: int = Field(default=0, ge=0, description="Which appearance of `replace`, from 0")
    base_revision: int | None = Field(default=None, description="409 if the doc changed since")


class DocRef(BaseModel):
    id: str
    title: str
    icon: str | None
    kind: Kind = "page"


class DocRevisionOut(BaseModel):
    id: str
    doc_id: str
    revision_no: int
    filename: str
    content_type: str
    size: int
    note: str = Field(description="What changed in this revision")
    uploaded_by: str
    created_at: datetime
    superseded_at: datetime | None = Field(description="When a later revision replaced it")
    restored_from: int | None = Field(description="The revision a restore copied")
    has_text: bool = Field(
        default=False,
        description="Indexed: text was extracted on upload (text files, CSV/XLSX, DOCX, PDF), "
        "so search sees inside it and compare can diff it. False for types without text and "
        "for a file over the server's extraction cap (SLATE_DOC_EXTRACT_MAX_BYTES, 64 MiB "
        "inflated by default) or one it could not read: still stored and viewable, not indexed",
    )
    url: str = Field(description="GET the bytes here (`?download=true` to save)")


class PinnedOut(BaseModel):
    revision_no: int
    note: str | None
    file_id: str | None = Field(description="The pinned revision's doc-file id")


class PinnedTopOut(BaseModel):
    at: datetime
    by: str | None = Field(description="Account id of who pinned it")


class DocSummaryOut(BaseModel):
    id: str
    kind: Kind = Field(description="`page` (Markdown), `file` (uploaded, with revisions), `folder`")
    file: DocRevisionOut | None = Field(description="A file doc's current revision")
    pinned: PinnedOut | None = Field(
        default=None, description="A file doc's pinned revision: the one in use"
    )
    pinned_to_top: PinnedTopOut | None = Field(
        default=None,
        description="Pinned to the top of its drawer or folder listing (shared), or null",
    )
    title: str
    icon: str | None
    parent_id: str | None
    section_id: str | None = Field(description="Top pages only; subpages inherit their top's")
    list_id: str | None
    position: int
    revision: int = Field(description="Goes up on every title or body change")
    version_no: int = Field(description="The newest page-history version")
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    archived_at: datetime | None
    comment_count: int = Field(default=0, description="Comments on the doc (both surfaces)")
    open_threads: int = Field(default=0, description="Open review threads")
    tags: list[str] = Field(default_factory=list, description="Tag names, sorted")
    snippet: str | None = Field(default=None, description="With q: text around the first match")


class HeadingOut(BaseModel):
    level: int
    text: str
    line: int = Field(description="0-based line in body_md")


class DocFileOut(BaseModel):
    id: str
    doc_id: str
    filename: str
    content_type: str
    size: int
    uploaded_by: str
    created_at: datetime
    url: str
    markdown: str = Field(description="Paste into body_md to show it")


class FactOut(BaseModel):
    key: str
    value: str


class DocOut(DocSummaryOut):
    body_md: str
    path: list[DocRef] = Field(description="Ancestors, top page first")
    children: list[DocSummaryOut]
    outline: list[HeadingOut]
    facts: list[FactOut] = Field(
        default_factory=list,
        description="Reference facts, when the body starts with a ```facts block of "
        "`key: value` lines (one per line, in order, duplicates kept): a policy number, a "
        "renewal date, a VIN. Write them with `body_md`; search finds a value. Empty otherwise",
    )
    tasks: list[ItemOut] = Field(description="Tasks embedded with [[task:<id>]], in page order")
    missing_tasks: list[str] = Field(description="Embedded task ids that no longer exist")
    docs: list[DocRef] = Field(description="Pages linked with [[doc:<id>]]")
    backlinks: list[DocRef] = Field(description="Pages that link or embed this one")
    files: list[DocFileOut] = Field(description="Images uploaded into the body")
    revisions: list[DocRevisionOut] = Field(description="A file doc's revisions, newest first")
    words: int


class VersionOut(BaseModel):
    id: str
    doc_id: str
    version_no: int
    title: str
    author_id: str
    created_at: datetime
    updated_at: datetime = Field(description="Its last save (autosaves fold into one version)")
    note: str | None
    chars: int
    body_md: str | None = None


class DiffOut(BaseModel):
    doc_id: str
    from_version: int
    to_version: int
    title_before: str
    title_after: str
    added: int
    removed: int
    diff: str = Field(description="Unified diff of the body")


class CellChange(BaseModel):
    row: int
    col: int
    before: str
    after: str


class SheetDiff(BaseModel):
    name: str = Field(description="The sheet, or the CSV's filename")
    changed: list[CellChange]
    added_rows: list[int] = Field(description="Row indexes only in the newer revision")
    removed_rows: list[int] = Field(description="Row indexes only in the older revision")
    rows: list[list[str]] = Field(description="The newer table")
    before_rows: list[list[str]] = Field(description="The older table")


class CompareOut(BaseModel):
    doc_id: str
    from_revision: int
    to_revision: int
    from_: DocRevisionOut = Field(alias="from")
    to: DocRevisionOut
    kind: Literal["text", "table", "none"] = Field(
        description="text: a unified diff (PDFs: of their extracted text, approximate); table: "
        "cell changes per sheet; none: no server diff (images, archives, CAD), compare by eye"
    )
    approximate: bool = Field(description="True for PDF text: layout is lost in extraction")
    diff: str
    added: int
    removed: int
    sheets: list[SheetDiff]
    message: str | None = Field(
        default=None,
        description="Why `kind` is `none` for a type that normally compares: a revision is not "
        "indexed (`has_text` false: over the server's extraction cap, unreadable, or uploaded "
        "before indexing). Compare reads only what upload stored; it never parses a file.",
    )

    model_config = ConfigDict(populate_by_name=True)


class HubCounts(BaseModel):
    docs: int
    files: int
    open_threads: int


class DrawerCounts(BaseModel):
    docs: int
    files: int
    folders: int
    open_threads: int


class HubNeed(BaseModel):
    kind: Literal["ask", "thread"] = Field(
        description="ask: addressed to you and open; thread: an open review thread by someone "
        "else on a doc you touched"
    )
    doc: DocRef
    list_id: str | None = Field(description="The drawer the doc is in, if any")
    comment: dict
    url: str = Field(description="Board link that opens the panel on the thread")


class HubDrawer(BaseModel):
    list: ListOut
    counts: DrawerCounts
    needs_you: int = Field(description="How many of needs_you are in this drawer")


class SectionDocumentsOut(BaseModel):
    drawers: list[HubDrawer]
    pages: list[DocSummaryOut] = Field(description="Top-level pages outside any drawer")
    needs_you: list[HubNeed]
    counts: HubCounts


class DrawerEntryOut(DocSummaryOut):
    children_count: int = Field(description="Entries inside (a folder's size)")


class DrawerOut(BaseModel):
    list: ListOut
    folder: DocSummaryOut | None = Field(description="The folder being looked at, if any")
    path: list[DocRef] = Field(description="Folders from the top down to `folder`")
    entries: list[DrawerEntryOut] = Field(description="Folders first by position, then docs")
    counts: DrawerCounts


class PinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=200, description="Why this one is in use")


class DocTaskOut(BaseModel):
    item: ItemOut
    doc: DocOut


class DeletedOut(BaseModel):
    deleted: list[str] = Field(description="The page and its subpages, subpages first")


class DocTemplateOut(BaseModel):
    key: str
    label: str
    title: str
    icon: str
    body_md: str


class Base64FileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(
        description="The file, base64-encoded (up to 10 MB decoded in a page, 25 MB a revision)"
    )
    content_type: str | None = Field(default=None, description="MIME type; guessed if omitted")


class Base64RevisionIn(Base64FileIn):
    note: str = Field(min_length=1, max_length=200, description="What changed in this revision")


class PromoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200, description="Default: the filename")
    parent_id: str | None = Field(default=None, description="Under this page")
    list_id: str | None = Field(default=None, description="Or a top doc in this list")
    section_id: str | None = Field(default=None, description="Or in this section")
    note: str | None = Field(default=None, max_length=200, description="Revision 1's note")


def out(session: Any, doc: Any) -> dict:
    return docs.doc_out(session, doc)


@router.get("/docs", response_model=list[DocSummaryOut])
def list_docs(
    _: ReadAuth,
    session: DbSession,
    q: str | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    parent_id: str | None = None,
    workspace: bool = False,
    archived: bool = False,
    embeds: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    kind: Annotated[str | None, Query(description="page or file")] = None,
    tag: Annotated[str | None, Query(description="A tag name or id")] = None,
) -> list[dict]:
    """Pages and files in tree order, without bodies. `q` searches titles, bodies, filenames
    and the text inside files (each hit has a `snippet`); `list_id`/`section_id` = top docs
    there and all under them; `workspace` = docs in no list or section; `embeds=<task or doc
    id>` = pages that embed or link it; `archived` = the archived docs themselves; `kind` and
    `tag` narrow further."""
    rows = docs.list_docs(
        session,
        q=q,
        list_id=list_id,
        section_id=section_id,
        parent_id=parent_id,
        workspace=workspace,
        archived=archived,
        embeds=embeds,
        limit=limit,
        kind=kind,
        tag=tag,
    )
    return docs.summaries(session, rows, q)


@router.get("/docs/templates", response_model=list[DocTemplateOut])
def templates(_: ReadAuth) -> list[dict]:
    """Starting points for a new page (`template` on create)."""
    return docs.templates()


@router.post("/docs", status_code=status.HTTP_201_CREATED, response_model=DocOut)
def create_doc(body: DocIn, auth: WriteAuth, session: DbSession) -> dict:
    """A new page: in a list, a section, the workspace (neither), or under `parent_id`."""
    doc = docs.create_doc(session, auth.actor, **body.model_dump(exclude_none=True))
    return out(session, doc)


@router.get("/docs/{doc_id}", response_model=DocOut)
def get_doc(doc_id: str, _: ReadAuth, session: DbSession) -> dict:
    """The page with its body, path, subpages, outline, embedded tasks, links and files."""
    return out(session, docs.get_doc(session, doc_id))


@router.patch("/docs/{doc_id}", response_model=DocOut)
def update_doc(doc_id: str, body: DocPatch, auth: WriteAuth, session: DbSession) -> dict:
    """Edit title, icon or body. `append_md` and `replace_section` change part of the body;
    pass `base_revision` to get a 409 instead of overwriting someone else's edit."""
    changes = body.model_dump(exclude_unset=True)
    if body.replace_section is not None:
        changes["replace_section"] = body.replace_section.model_dump()
    return out(session, docs.update_doc(session, auth.actor, doc_id, changes))


@router.post("/docs/{doc_id}/move", response_model=DocOut)
def move_doc(doc_id: str, body: DocMoveIn, auth: WriteAuth, session: DbSession) -> dict:
    """Move a page and its subpages under another page, or to the top of a list, a section or
    the workspace (no parent and no place)."""
    return out(session, docs.move_doc(session, auth.actor, doc_id, body.model_dump()))


@router.post("/docs/{doc_id}/archive", response_model=DocOut)
def archive_doc(doc_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Hide a page and its subpages."""
    return out(session, docs.archive_doc(session, auth.actor, doc_id))


@router.post("/docs/{doc_id}/unarchive", response_model=DocOut)
def unarchive_doc(doc_id: str, auth: WriteAuth, session: DbSession) -> dict:
    return out(session, docs.unarchive_doc(session, auth.actor, doc_id))


@router.delete("/docs/{doc_id}", response_model=DeletedOut)
def delete_doc(doc_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Delete an archived page and its subpages for good (the history of events stays)."""
    return {"deleted": docs.delete_doc(session, auth.actor, doc_id)}


@router.post("/docs/{doc_id}/duplicate", status_code=status.HTTP_201_CREATED, response_model=DocOut)
def duplicate_doc(doc_id: str, body: DocDuplicateIn, auth: WriteAuth, session: DbSession) -> dict:
    """Copy a page (and its subpages) next to the original."""
    copy = docs.duplicate_doc(session, auth.actor, doc_id, title=body.title, subpages=body.subpages)
    return out(session, copy)


@router.get("/docs/{doc_id}/versions", response_model=list[VersionOut])
def versions(doc_id: str, _: ReadAuth, session: DbSession) -> list[dict]:
    """Page history, newest first, without bodies (one version per sitting of edits)."""
    return [docs.version_out(v, body=False) for v in docs.versions_of(session, doc_id)]


@router.get("/docs/{doc_id}/versions/{version_no}", response_model=VersionOut)
def version(doc_id: str, version_no: int, _: ReadAuth, session: DbSession) -> dict:
    return docs.version_out(docs.get_version(session, doc_id, version_no))


@router.post("/docs/{doc_id}/versions/{version_no}/restore", response_model=DocOut)
def restore(doc_id: str, version_no: int, auth: WriteAuth, session: DbSession) -> dict:
    """Bring back an older version's title and body (as a new version)."""
    return out(session, docs.restore_version(session, auth.actor, doc_id, version_no))


@router.get("/docs/{doc_id}/diff", response_model=DiffOut)
def diff(
    doc_id: str,
    _: ReadAuth,
    session: DbSession,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int | None, Query(alias="to", ge=1)] = None,
) -> dict:
    """What changed in the body between two versions (`to` default: the current text)."""
    return docs.diff(session, doc_id, from_version, to_version)


@router.post("/docs/{doc_id}/tasks", status_code=status.HTTP_201_CREATED, response_model=DocTaskOut)
def create_task(doc_id: str, body: DocTaskIn, auth: WriteAuth, session: DbSession) -> dict:
    """File one task from the page (create task from selection): `replace` names the text
    that becomes the task's live card; the task links back to the page."""
    from slate.service import items

    fields = body.model_dump(exclude_none=True)
    item, doc = docs.create_task(session, auth.actor, doc_id, **fields)
    return {"item": items.views(session, [item])[0], "doc": out(session, doc)}


@router.post(
    "/docs/{doc_id}/files",
    status_code=status.HTTP_201_CREATED,
    response_model=DocFileOut,
    openapi_extra={
        "requestBody": {"content": {"application/octet-stream": {"schema": {"type": "string"}}}}
    },
)
async def add_file(
    doc_id: str,
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    auth: WriteAuth,
    session: DbSession,
) -> dict:
    """Upload an image or file into the page (raw body, up to 10 MB); paste its `markdown`."""
    data = await read_body(request, docs.MAX_FILE_BYTES)
    # The body is read on the event loop; the write (which waits for the write lock) runs in
    # the threadpool like any sync route, so a held lock never stalls other requests.
    f = await run_in_threadpool(
        docs.add_file,
        session,
        auth.actor,
        doc_id,
        filename=filename,
        data=data,
        content_type=request.headers.get("content-type"),
    )
    return docs.file_out(f)


@router.post(
    "/docs/{doc_id}/files/base64", status_code=status.HTTP_201_CREATED, response_model=DocFileOut
)
def add_file_base64(doc_id: str, body: Base64FileIn, auth: WriteAuth, session: DbSession) -> dict:
    """The same upload for clients that can only send JSON."""
    f = docs.add_file(
        session,
        auth.actor,
        doc_id,
        filename=body.filename,
        data=attachments_svc.decode_base64(body.content_base64),
        content_type=body.content_type,
    )
    return docs.file_out(f)


@router.get("/sections/{section_id}/documents", response_model=SectionDocumentsOut)
def section_documents(section_id: str, auth: ReadAuth, session: DbSession) -> dict:
    """The project home's documents hub: the section's drawers with counts, its pages
    outside any drawer, and what needs you (open Asks to you; open review threads by others
    on docs you touched)."""
    from slate.service import doc_hub

    out = doc_hub.section_documents(session, section_id, auth.actor.account_id)
    out["drawers"] = [{**d, "list": list_out(session, d["list"])} for d in out["drawers"]]
    return out


@router.get("/lists/{list_id}/drawer", response_model=DrawerOut)
def drawer(
    list_id: str,
    _: ReadAuth,
    session: DbSession,
    folder: Annotated[str | None, Query(description="A folder doc id to look inside")] = None,
) -> dict:
    """A drawer's entries at its top or inside `folder`: folders and documents in tree order,
    each with its current and pinned revision, tags, thread counts and (for a folder) how
    many entries it holds; plus the folder path and the drawer's counts."""
    out = docs.drawer_listing(session, list_id, folder)
    out["list"] = list_out(session, out["list"])
    return out


@router.post(
    "/docs/{doc_id}/to-drawer", status_code=status.HTTP_201_CREATED, response_model=ListOut
)
def to_drawer(doc_id: str, auth: WriteAuth, session: DbSession) -> ListOut:
    """Turn a top-level page into a drawer of the same name in its section: its subpages
    become the drawer's entries and the page itself moves in too (its text is kept)."""
    return list_out(session, docs.to_drawer(session, auth.actor, doc_id))


@router.post("/docs/{doc_id}/revisions/{revision_no}/pin", response_model=DocOut)
def pin_revision(
    doc_id: str, revision_no: int, body: PinIn, auth: WriteAuth, session: DbSession
) -> dict:
    """Mark a revision as the one in use. One pin per document;
    pinning another revision moves it."""
    return out(session, docs.pin_revision(session, auth.actor, doc_id, revision_no, body.note))


@router.delete("/docs/{doc_id}/pin", response_model=DocOut)
def unpin_revision(doc_id: str, auth: WriteAuth, session: DbSession) -> dict:
    return out(session, docs.unpin_revision(session, auth.actor, doc_id))


@router.post("/docs/{doc_id}/top", response_model=DocOut)
def pin_to_top(doc_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Pin a document (file, page or folder) to the top of its drawer or folder listing, for
    everyone. Pinned entries come first in `GET /lists/{id}/drawer`, oldest pin first."""
    return out(session, docs.pin_to_top(session, auth.actor, doc_id))


@router.delete("/docs/{doc_id}/top", response_model=DocOut)
def unpin_from_top(doc_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Take a document off the top of its listing."""
    return out(session, docs.unpin_from_top(session, auth.actor, doc_id))


@router.get("/docs/{doc_id}/revisions", response_model=list[DocRevisionOut])
def revisions(doc_id: str, _: ReadAuth, session: DbSession) -> list[dict]:
    """A file doc's revisions, newest first (metadata; the bytes are at each `url`)."""
    return [docs.revision_out(f) for f in docs.revisions_of(session, doc_id)]


@router.get(
    "/docs/{doc_id}/revisions/compare", response_model=CompareOut, response_model_by_alias=True
)
def compare_revisions(
    doc_id: str,
    _: ReadAuth,
    session: DbSession,
    from_no: Annotated[int, Query(alias="from", ge=1)],
    to_no: Annotated[int | None, Query(alias="to", ge=1)] = None,
) -> dict:
    """What changed between two revisions of a file doc (`to` default: the current one), by
    type: text files line by line; CSV, TSV and XLSX cell by cell per sheet; PDFs by their
    extracted text (approximate); images and the rest have no server diff."""
    from slate.service import doc_compare

    return doc_compare.compare(session, doc_id, from_no, to_no)


@router.post(
    "/docs/{doc_id}/revisions",
    status_code=status.HTTP_201_CREATED,
    response_model=DocRevisionOut,
    openapi_extra={
        "requestBody": {"content": {"application/octet-stream": {"schema": {"type": "string"}}}}
    },
)
async def add_revision(
    doc_id: str,
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    note: Annotated[str, Query(min_length=1, max_length=200, description="What changed")],
    auth: WriteAuth,
    session: DbSession,
) -> dict:
    """Upload the next revision of a file doc (raw body, up to 25 MB). The previous revision
    stays, marked superseded. `note` says what changed; it is required."""
    data = await read_body(request, docs.MAX_REVISION_BYTES)
    # Reading the file's text and the write both run in the threadpool (see `add_file`).
    f = await run_in_threadpool(
        docs.add_revision,
        session,
        auth.actor,
        doc_id,
        filename=filename,
        data=data,
        note=note,
        content_type=request.headers.get("content-type"),
    )
    return docs.revision_out(f)


@router.post(
    "/docs/{doc_id}/revisions/base64",
    status_code=status.HTTP_201_CREATED,
    response_model=DocRevisionOut,
)
def add_revision_base64(
    doc_id: str, body: Base64RevisionIn, auth: WriteAuth, session: DbSession
) -> dict:
    """The same upload for clients that can only send JSON."""
    f = docs.add_revision(
        session,
        auth.actor,
        doc_id,
        filename=body.filename,
        data=attachments_svc.decode_base64(body.content_base64),
        note=body.note,
        content_type=body.content_type,
    )
    return docs.revision_out(f)


@router.post("/docs/{doc_id}/revisions/{revision_no}/restore", response_model=DocOut)
def restore_revision(doc_id: str, revision_no: int, auth: WriteAuth, session: DbSession) -> dict:
    """Make an older revision current again, as a new revision with its bytes."""
    docs.restore_revision(session, auth.actor, doc_id, revision_no)
    return out(session, docs.get_doc(session, doc_id))


@router.post(
    "/attachments/{attachment_id}/promote",
    status_code=status.HTTP_201_CREATED,
    response_model=DocOut,
)
def promote_attachment(
    attachment_id: str, body: PromoteIn, auth: WriteAuth, session: DbSession
) -> dict:
    """Move a task's attachment into Docs & Files: a new file doc with the bytes as revision
    1; the task loses the attachment and gets a comment linking the doc."""
    doc = docs.promote_attachment(
        session, auth.actor, attachment_id, **body.model_dump(exclude_none=True)
    )
    return out(session, doc)


@router.get("/doc-files/{file_id}", response_class=Response)
def download_file(
    file_id: str, _: ReadAuth, session: DbSession, download: bool = False
) -> Response:
    """The file's bytes; images show inline unless `?download=true`."""
    f = docs.get_file(session, file_id)
    inline = not download and f.content_type.startswith(("image/", "application/pdf", "text/"))
    return Response(
        content=f.data,
        media_type=f.content_type,
        headers={
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; "
            f"filename*=UTF-8''{quote(f.filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": CSP,
        },
    )


@router.delete("/doc-files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_file(file_id: str, auth: WriteAuth, session: DbSession) -> Response:
    docs.remove_file(session, auth.actor, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
