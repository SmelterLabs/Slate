"""Docs: ClickUp-style pages of Markdown that nest, live in a list, a section or the whole
workspace, keep a page history, and embed tasks as live cards.

A page is one `docs` row. Only a top page carries a location (`list_id`, and `section_id` for
quick filtering; neither = the workspace); subpages inherit their top page's. Every change goes
through `apply_change()`:

- `doc.created`, `doc.updated` (field `title`, `icon` or `body_md`, before -> after),
  `doc.moved` (before/after `{parent_id, section_id, list_id, position}`),
  `doc.archived`, `doc.unarchived`, `doc.deleted`, `doc.version_restored`,
  `doc.file_added`, `doc.file_removed`, `doc.revision_added`. Subject = the doc; `item_id`
  is empty.

File docs (`kind = file`): an uploaded PDF, spreadsheet or photo as a doc in the same tree as
pages. Its `doc_files` rows with a `revision_no` are its revisions, 1 upward: `add_revision`
adds the next one (a note saying what changed is required) and stamps the previous one
superseded; `restore_revision` copies an old one as the next; nothing is ever deleted.
`body_md` is then a description shown above the file.
- `doc.mentioned` when a save adds `@handle`s the page did not have (`after` = `{doc_id,
  title, mentions, excerpt}`), so the inbox, check-in, push and webhooks hear about it like a
  comment mention. Autosave sends the body many times; only newly added handles count. A push
  agent among them is woken for a doc turn (service/docwake.py).

Page history: a title or body save by the account that made the newest version, within
`FOLD_MINUTES` of its last save, folds into that version; anything else (another account, a
pause, `new_version`, a restore) starts a new one. The event log still has every save.

Embeds are plain text so agents read and write them without a special API:
`[[task:<id>]]` on a line of its own renders as a live task card on the board (inline, a chip),
`[[doc:<id>]]` links another page. `doc_out()` resolves both for the reader.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, load_only, undefer

from slate.ids import new_id
from slate.models import Doc, DocFile, DocTag, DocVersion, Item, List, Section, Tag, utcnow
from slate.service import access, guests
from slate.service import attachments as attachments_svc
from slate.service import items as items_svc
from slate.service import tags as tags_svc
from slate.service.comments import mentions_in
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Conflict, Invalid, NotFound
from slate.service.lists import get_list
from slate.service.sections import get_section
from slate.settings import settings

MAX_DEPTH = 8  # a top page is depth 1
MAX_BODY = 500_000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REVISION_BYTES = 25 * 1024 * 1024  # a file doc's revision, like a task attachment
KINDS = ("page", "file", "folder")
FOLD_MINUTES = 10
EMBED_RE = re.compile(r"\[\[(task|doc):([0-9A-Za-z]{12})\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# Starting points offered by "New doc" on the board and `template` on create.
TEMPLATES: dict[str, dict[str, str]] = {
    "blank": {"label": "Blank page", "title": "Untitled", "icon": "", "body_md": ""},
    "meeting": {
        "label": "Meeting notes",
        "title": "Meeting notes",
        "icon": "🗓️",
        "body_md": "## Attendees\n\n- \n\n## Agenda\n\n1. \n\n## Notes\n\n\n\n"
        "## Decisions\n\n- \n\n## Action items\n\n- [ ] \n",
    },
    "plan": {
        "label": "Project plan",
        "title": "Project plan",
        "icon": "🧭",
        "body_md": "## Goal\n\nWhat done looks like, in one paragraph.\n\n## Why\n\n\n\n"
        "## Milestones\n\n| Milestone | Owner | Due | Status |\n| --- | --- | --- | --- |\n"
        "|  |  |  |  |\n\n## Tasks\n\nSelect a line and choose **Create task** to file it.\n\n"
        "## Risks\n\n- \n\n## Notes\n\n",
    },
    "decision": {
        "label": "Decision record",
        "title": "Decision: ",
        "icon": "⚖️",
        "body_md": "## Context\n\n\n\n## Options\n\n1. \n2. \n\n## Decision\n\n\n\n"
        "## Consequences\n\n- \n",
    },
    "runbook": {
        "label": "Runbook",
        "title": "Runbook: ",
        "icon": "📘",
        "body_md": "## When to use this\n\n\n\n## Steps\n\n- [ ] \n- [ ] \n- [ ] \n\n"
        "## If it goes wrong\n\n\n\n## Contacts\n\n- \n",
    },
    "agent_log": {
        "label": "Agent work log",
        "title": "Work log",
        "icon": "🤖",
        "body_md": "Agents append dated entries here (`append_md`).\n\n## Log\n\n",
    },
}


# --- reads -----------------------------------------------------------------------------------


def get_doc(session: Session, doc_id: str) -> Doc:
    doc = session.get(Doc, doc_id)
    if doc is None:
        raise NotFound(f"doc {doc_id} not found")
    return doc


def fresh_doc(session: Session, doc_id: str) -> Doc:
    """The doc as the database has it now, body included, overwriting this session's copy.
    Read it first thing inside `apply_change` (nothing is dirty yet, so nothing flushes) when a
    check or an edit depends on the current text: the copy loaded before the write lock may
    be stale by the time the lock is held."""
    doc = session.scalar(
        select(Doc)
        .options(undefer(Doc.body_md))
        .where(Doc.id == doc_id)
        .execution_options(populate_existing=True)
    )
    if doc is None:
        raise NotFound(f"doc {doc_id} not found")
    return doc


def _all(session: Session, *, fresh: bool = False) -> list[Doc]:
    """Every doc without bodies: enough to walk the tree. `fresh` overwrites rows this session
    already holds with what the database has now (for a check made under the write lock)."""
    stmt = select(Doc)
    if fresh:
        stmt = stmt.execution_options(populate_existing=True)
    return list(
        session.scalars(
            stmt.options(
                load_only(
                    Doc.id,
                    Doc.kind,
                    Doc.pinned_revision_no,
                    Doc.pinned_note,
                    Doc.top_pinned_at,
                    Doc.top_pinned_by,
                    Doc.title,
                    Doc.icon,
                    Doc.parent_id,
                    Doc.section_id,
                    Doc.list_id,
                    Doc.position,
                    Doc.revision,
                    Doc.version_no,
                    Doc.created_by,
                    Doc.created_at,
                    Doc.updated_by,
                    Doc.updated_at,
                    Doc.archived_at,
                )
            ).order_by(Doc.position, Doc.created_at)
        )
    )


def _ancestors(by_id: Mapping[str, Doc], doc: Doc) -> list[Doc]:
    """Parent first, top page last. Stops on a broken link rather than looping."""
    out: list[Doc] = []
    seen = {doc.id}
    cur = by_id.get(doc.parent_id) if doc.parent_id else None
    while cur is not None and cur.id not in seen:
        out.append(cur)
        seen.add(cur.id)
        cur = by_id.get(cur.parent_id) if cur.parent_id else None
    return out


def root_of(session: Session, doc: Doc) -> Doc:
    cur = doc
    seen = {doc.id}
    while cur.parent_id and cur.parent_id not in seen:
        seen.add(cur.parent_id)
        cur = get_doc(session, cur.parent_id)
    return cur


def _descendants(docs: Sequence[Doc], doc_id: str) -> list[Doc]:
    children: dict[str, list[Doc]] = {}
    for d in docs:
        if d.parent_id:
            children.setdefault(d.parent_id, []).append(d)
    out: list[Doc] = []
    seen = {doc_id}
    stack = list(children.get(doc_id, []))
    while stack:
        d = stack.pop()
        if d.id in seen:  # a broken tree (a parent cycle) must not walk forever
            continue
        seen.add(d.id)
        out.append(d)
        stack.extend(children.get(d.id, []))
    return out


def _hidden_places(session: Session) -> tuple[set[str], set[str]]:
    lists = set(session.scalars(select(List.id).where(List.archived_at.is_not(None))))
    sections = set(session.scalars(select(Section.id).where(Section.archived_at.is_not(None))))
    return lists, sections


def _live(session: Session, docs: Sequence[Doc], by_id: Mapping[str, Doc]) -> list[Doc]:
    """The docs a listing shows: not archived, under no archived page, and not in an archived
    list or section."""
    dead_lists, dead_sections = _hidden_places(session)

    def visible(d: Doc) -> bool:
        chain = _ancestors(by_id, d)
        if d.archived_at is not None or any(a.archived_at is not None for a in chain):
            return False
        t = chain[-1] if chain else d
        return t.list_id not in dead_lists and t.section_id not in dead_sections

    return [d for d in docs if visible(d)]


def list_docs(
    session: Session,
    *,
    q: str | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    parent_id: str | None = None,
    workspace: bool = False,
    archived: bool = False,
    embeds: str | None = None,
    limit: int | None = None,
    kind: str | None = None,
    tag: str | None = None,
) -> list[Doc]:
    """Docs in tree order (siblings by position). Filters AND together. `kind` keeps pages or
    files; `tag` (a name or id) keeps docs carrying it.

    `list_id` / `section_id`: top pages there and everything under them. `workspace`: only
    pages not in any list or section. `parent_id`: direct subpages. `archived`: the archived
    pages themselves (not their subpages); otherwise archived pages, their subpages, and pages
    in archived lists or sections are hidden. `q`: case-insensitive substring of title or body.
    `embeds`: pages that embed or link that task or doc id.
    """
    docs = _all(session)
    by_id = {d.id: d for d in docs}

    def top(d: Doc) -> Doc:
        chain = _ancestors(by_id, d)
        return chain[-1] if chain else d

    if archived:
        pool = [d for d in docs if d.archived_at is not None]
    else:
        pool = _live(session, docs, by_id)
    if list_id is not None:
        pool = [d for d in pool if top(d).list_id == list_id]
    if section_id is not None:
        pool = [d for d in pool if top(d).section_id == section_id]
    if workspace:
        pool = [d for d in pool if top(d).section_id is None and top(d).list_id is None]
    if parent_id is not None:
        pool = [d for d in pool if d.parent_id == parent_id]
    if kind is not None:
        if kind not in KINDS:
            raise Invalid(f"kind must be one of {', '.join(KINDS)}")
        pool = [d for d in pool if d.kind == kind]
    if tag is not None:
        tag_row = tags_svc.get_tag(session, tag)
        tagged = set(session.scalars(select(DocTag.doc_id).where(DocTag.tag_id == tag_row.id)))
        pool = [d for d in pool if d.id in tagged]
    if q or embeds:
        stmt = select(Doc.id)
        if q:
            from slate.service.search import like_pattern  # search imports this module

            like = like_pattern(q)  # the caller's % and _ are plain characters
            # A file matches on its current revision's name and text, the ones a hit quotes.
            newest = (
                select(DocFile.doc_id, func.max(DocFile.revision_no).label("no"))
                .where(DocFile.revision_no.is_not(None))
                .group_by(DocFile.doc_id)
                .subquery()
            )
            named = (
                select(DocFile.doc_id)
                .join(
                    newest,
                    and_(DocFile.doc_id == newest.c.doc_id, DocFile.revision_no == newest.c.no),
                )
                .where(
                    or_(
                        func.lower(DocFile.filename).like(like, escape="\\"),
                        func.lower(DocFile.text).like(like, escape="\\"),
                    ),
                )
                .scalar_subquery()
            )
            stmt = stmt.where(
                or_(
                    func.lower(Doc.title).like(like, escape="\\"),
                    func.lower(Doc.body_md).like(like, escape="\\"),
                    Doc.id.in_(named),
                )
            )
        if embeds:
            stmt = stmt.where(
                or_(
                    Doc.body_md.contains(f"[[task:{embeds}]]"),
                    Doc.body_md.contains(f"[[doc:{embeds}]]"),
                )
            )
        hits = set(session.scalars(stmt))
        pool = [d for d in pool if d.id in hits]
    ordered = _tree_order(pool)
    return ordered[:limit] if limit else ordered


def in_scope(
    session: Session,
    *,
    list_id: str | None = None,
    section_id: str | None = None,
    include_archived: bool = True,
) -> list[Doc]:
    """Pages for an export: everything under top pages in the list or section (every page when
    neither is given), archived ones too unless `include_archived` is false."""
    docs = _all(session)
    by_id = {d.id: d for d in docs}

    def top(d: Doc) -> Doc:
        chain = _ancestors(by_id, d)
        return chain[-1] if chain else d

    def keep(d: Doc) -> bool:
        if list_id is not None and top(d).list_id != list_id:
            return False
        if section_id is not None and top(d).section_id != section_id:
            return False
        if include_archived:
            return True
        return d.archived_at is None and all(a.archived_at is None for a in _ancestors(by_id, d))

    return _tree_order([d for d in docs if keep(d)])


def _tree_order(docs: Sequence[Doc]) -> list[Doc]:
    """Depth-first, siblings by position; docs whose parent is not in the set start a branch."""
    ids = {d.id for d in docs}
    children: dict[str | None, list[Doc]] = {}
    for d in docs:
        key = d.parent_id if d.parent_id in ids else None
        children.setdefault(key, []).append(d)
    for group in children.values():
        group.sort(key=lambda d: (d.position, d.created_at, d.id))
    out: list[Doc] = []
    seen: set[str] = set()

    def walk(key: str | None) -> None:
        for d in children.get(key, []):
            if d.id in seen:
                continue
            seen.add(d.id)
            out.append(d)
            walk(d.id)

    walk(None)
    return out


def snippet(body: str, q: str, width: int = 80) -> str | None:
    at = body.lower().find(q.lower())
    if at < 0:
        return None
    start = max(0, at - width // 2)
    end = min(len(body), at + len(q) + width // 2)
    text = " ".join(body[start:end].split())
    return ("…" if start else "") + text + ("…" if end < len(body) else "")


def doc_summary(doc: Doc, current: DocFile | None = None) -> dict[str, Any]:
    """The index row. `current` is a file doc's newest revision (see `current_revisions`)."""
    return {
        "id": doc.id,
        "kind": doc.kind,
        "file": revision_out(current) if current is not None else None,
        "pinned": (
            {
                "revision_no": doc.pinned_revision_no,
                "note": doc.pinned_note,
                "file_id": getattr(current, "pinned_file_id", None),
            }
            if doc.pinned_revision_no is not None
            else None
        ),
        "pinned_to_top": (
            {"at": doc.top_pinned_at, "by": doc.top_pinned_by}
            if doc.top_pinned_at is not None
            else None
        ),
        "title": doc.title,
        "icon": doc.icon,
        "parent_id": doc.parent_id,
        "section_id": doc.section_id,
        "list_id": doc.list_id,
        "position": doc.position,
        "revision": doc.revision,
        "version_no": doc.version_no,
        "created_by": doc.created_by,
        "created_at": doc.created_at,
        "updated_by": doc.updated_by,
        "updated_at": doc.updated_at,
        "archived_at": doc.archived_at,
    }


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


def tags_of(session: Session, doc_ids: Sequence[str]) -> dict[str, list[str]]:
    """{doc_id: sorted tag names} for the given docs (one query)."""
    out: dict[str, list[str]] = {d: [] for d in doc_ids}
    if doc_ids:
        for doc_id, name in session.execute(
            select(DocTag.doc_id, Tag.name)
            .join(Tag, Tag.id == DocTag.tag_id)
            .where(DocTag.doc_id.in_(doc_ids))
            .order_by(Tag.name)
        ):
            out[doc_id].append(name)
    return out


def _with_counts(session: Session, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add `comment_count`, `open_threads` (open review threads) and `tags` to summary rows."""
    from slate.service.doc_threads import counts

    ids = [r["id"] for r in rows]
    found = counts(session, ids)
    tags = tags_of(session, ids)
    for r in rows:
        r["comment_count"], r["open_threads"] = found.get(r["id"], (0, 0))
        r["tags"] = tags.get(r["id"], [])
    return rows


def _set_tags(cx: Change, doc: Doc, names: Sequence[str]) -> None:
    """Replace the doc's tags; unknown names are created. One `doc.updated` (field `tags`)."""
    session = cx.session
    wanted = {t.id: t.name for t in tags_svc.ensure_tags(session, cx.actor, names)}
    rows = list(session.scalars(select(DocTag).where(DocTag.doc_id == doc.id)))
    current = {r.tag_id for r in rows}
    if current == set(wanted):
        return
    current_names = dict(session.execute(select(Tag.id, Tag.name).where(Tag.id.in_(current))).all())
    removed = [r for r in rows if r.tag_id not in wanted]
    added = [DocTag(doc_id=doc.id, tag_id=t) for t in wanted if t not in current]
    for r in removed:
        session.delete(r)
    for r in added:
        session.add(r)
    cx.record(
        "doc.updated",
        subject_id=doc.id,
        field="tags",
        before=sorted(current_names.values()),
        after=sorted(wanted.values()),
        covers=[*removed, *added],
    )


def summaries(session: Session, docs: Sequence[Doc], q: str | None = None) -> list[dict[str, Any]]:
    current = current_revisions(session, docs)
    out = _with_counts(session, [doc_summary(d, current.get(d.id)) for d in docs])
    if q and docs:
        bodies = dict(
            session.execute(
                select(Doc.id, Doc.body_md).where(Doc.id.in_([d.id for d in docs]))
            ).all()
        )
        for row in out:
            row["snippet"] = snippet(bodies.get(row["id"], ""), q)
        # A file: the hit is in its current revision's extracted text, read for those alone.
        need = {
            current[row["id"]].id: row
            for row in out
            if row["snippet"] is None and getattr(current.get(row["id"]), "has_text", False)
        }
        if need:
            for file_id, text in session.execute(
                select(DocFile.id, DocFile.text).where(DocFile.id.in_(list(need)))
            ):
                need[file_id]["snippet"] = snippet(text or "", q)
    return out


def embeds_in(body: str) -> dict[str, list[str]]:
    tasks: list[str] = []
    docs: list[str] = []
    for kind, ref in EMBED_RE.findall(body):
        bucket = tasks if kind == "task" else docs
        if ref not in bucket:
            bucket.append(ref)
    return {"tasks": tasks, "docs": docs}


def outline(body: str) -> list[dict[str, Any]]:
    """Headings in order (outside fenced code): the page's table of contents."""
    out: list[dict[str, Any]] = []
    fenced = False
    for n, line in enumerate(body.split("\n")):
        if line.startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING_RE.match(line)
        if m and m.group(2):
            out.append({"level": len(m.group(1)), "text": m.group(2), "line": n})
    return out


_FACTS_OPEN_RE = re.compile(r"^\s*```\s*facts\s*$", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")


def parse_facts(body: str) -> list[dict[str, str]]:
    """The page's reference facts: a fenced ```facts block at the very start of the body (blank
    lines before it allowed), one `key: value` per line. Keys and values are trimmed plain text;
    a value may be empty; a line without a key is skipped; duplicate keys keep their order
    (nothing is merged). Anywhere else in the page a ```facts block is ordinary code. The
    block stays in `body_md`, so agents write it with the usual `update`, search already
    finds a value, and the editor keeps it editable as text."""
    lines = body.split("\n")
    n = 0
    while n < len(lines) and not lines[n].strip():
        n += 1
    if n >= len(lines) or not _FACTS_OPEN_RE.match(lines[n]):
        return []
    out: list[dict[str, str]] = []
    for line in lines[n + 1 :]:
        if _FENCE_CLOSE_RE.match(line):
            return out
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out.append({"key": key.strip(), "value": value.strip()})
    return []  # never closed: not a facts block


def _ref(d: Doc) -> dict[str, Any]:
    return {"id": d.id, "title": d.title, "icon": d.icon, "kind": d.kind}


def doc_out(session: Session, doc: Doc) -> dict[str, Any]:
    """The whole page: body, path, outline, embedded tasks (as item views), linked pages,
    pages that link here, and uploaded files."""
    docs = _all(session)
    by_id = {d.id: d for d in docs}
    refs = embeds_in(doc.body_md)
    tasks = list(
        session.scalars(select(Item).where(Item.id.in_(refs["tasks"]), Item.trashed_at.is_(None)))
    )
    order = {ref: n for n, ref in enumerate(refs["tasks"])}
    tasks.sort(key=lambda i: order[i.id])
    linked = [by_id[ref] for ref in refs["docs"] if ref in by_id]
    # Pages that link here, from the tree already in hand: one query over bodies, for the
    # link form a doc id can take.
    linking = set(
        session.scalars(
            select(Doc.id).where(Doc.body_md.contains(f"[[doc:{doc.id}]]"), Doc.id != doc.id)
        )
    )
    backlinks = (
        _tree_order([d for d in _live(session, docs, by_id) if d.id in linking]) if linking else []
    )
    rows = _file_rows(session, DocFile.doc_id == doc.id, order_by=DocFile.created_at)
    files = [f for f in rows if f.revision_no is None]
    revisions = sorted((f for f in rows if f.revision_no is not None), key=lambda f: -f.revision_no)
    kids = [
        d for d in _tree_order([d for d in docs if d.parent_id == doc.id]) if d.archived_at is None
    ]
    kid_current = current_revisions(session, kids)
    current = revisions[0] if revisions else None
    if current is not None and doc.pinned_revision_no is not None:
        current.pinned_file_id = next(  # type: ignore[attr-defined]
            (f.id for f in revisions if f.revision_no == doc.pinned_revision_no), None
        )
    out = _with_counts(session, [doc_summary(doc, current)])[0]
    out.update(
        body_md=doc.body_md,
        path=[
            {"id": a.id, "title": a.title, "icon": a.icon} for a in reversed(_ancestors(by_id, doc))
        ],
        children=_with_counts(session, [doc_summary(d, kid_current.get(d.id)) for d in kids]),
        outline=outline(doc.body_md),
        facts=parse_facts(doc.body_md),
        tasks=items_svc.views(session, tasks),
        missing_tasks=[ref for ref in refs["tasks"] if ref not in {t.id for t in tasks}],
        docs=[_ref(d) for d in linked],
        backlinks=[{"id": d.id, "title": d.title, "icon": d.icon} for d in backlinks],
        files=[file_out(f) for f in files],
        revisions=[revision_out(f) for f in revisions],
        words=len(doc.body_md.split()),
    )
    return out


def versions_of(session: Session, doc_id: str) -> list[DocVersion]:
    get_doc(session, doc_id)
    return list(
        session.scalars(
            select(DocVersion)
            .where(DocVersion.doc_id == doc_id)
            .order_by(DocVersion.version_no.desc())
        )
    )


def get_version(session: Session, doc_id: str, version_no: int) -> DocVersion:
    v = session.scalar(
        select(DocVersion).where(DocVersion.doc_id == doc_id, DocVersion.version_no == version_no)
    )
    if v is None:
        get_doc(session, doc_id)
        raise NotFound(f"doc {doc_id} has no version {version_no}")
    return v


def version_out(v: DocVersion, *, body: bool = True) -> dict[str, Any]:
    out = {
        "id": v.id,
        "doc_id": v.doc_id,
        "version_no": v.version_no,
        "title": v.title,
        "author_id": v.author_id,
        "created_at": v.created_at,
        "updated_at": v.updated_at,
        "note": v.note,
        "chars": len(v.body_md),
    }
    if body:
        out["body_md"] = v.body_md
    return out


def diff(
    session: Session, doc_id: str, from_version: int, to_version: int | None = None
) -> dict[str, Any]:
    """A unified diff of the body from one version to another (default: the current text),
    plus counts. Agents read what changed in a page without fetching both bodies."""
    doc = get_doc(session, doc_id)
    old = get_version(session, doc_id, from_version)
    if to_version is None:
        new_title, new_body, to_no = doc.title, doc.body_md, doc.version_no
    else:
        new = get_version(session, doc_id, to_version)
        new_title, new_body, to_no = new.title, new.body_md, new.version_no
    lines = list(
        difflib.unified_diff(
            old.body_md.splitlines(),
            new_body.splitlines(),
            fromfile=f"v{old.version_no}",
            tofile=f"v{to_no}",
            lineterm="",
        )
    )
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return {
        "doc_id": doc_id,
        "from_version": old.version_no,
        "to_version": to_no,
        "title_before": old.title,
        "title_after": new_title,
        "added": added,
        "removed": removed,
        "diff": "\n".join(lines),
    }


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


def templates() -> list[dict[str, str]]:
    return [{"key": k, **v} for k, v in TEMPLATES.items()]


# --- helpers ---------------------------------------------------------------------------------


def _title(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise Invalid("title is empty")
    if len(text) > 200:
        raise Invalid("title is longer than 200 characters")
    return text


def _icon(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) > 16:
        raise Invalid("icon is one emoji (16 characters at most)")
    return text or None


def _body(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n")
    if len(text) > MAX_BODY:
        raise Invalid(f"body is longer than {MAX_BODY:,} characters")
    return text


def _place(
    session: Session, list_id: str | None, section_id: str | None
) -> tuple[str | None, str | None]:
    """A top page's (section_id, list_id); a list fixes its section."""
    if list_id is not None:
        lst = get_list(session, list_id)
        if section_id is not None and section_id != lst.section_id:
            raise Invalid("that list is in another section")
        return lst.section_id, lst.id
    if section_id is not None:
        return get_section(session, section_id).id, None
    return None, None


def _depth(by_id: Mapping[str, Doc], doc: Doc) -> int:
    return 1 + len(_ancestors(by_id, doc))


def _height(docs: Sequence[Doc], doc_id: str) -> int:
    """Levels in the subtree rooted at doc_id, counting itself."""
    children: dict[str, list[str]] = {}
    for d in docs:
        if d.parent_id:
            children.setdefault(d.parent_id, []).append(d.id)

    def h(i: str, seen: frozenset[str]) -> int:
        kids = [k for k in children.get(i, []) if k not in seen]
        return 1 + max((h(k, seen | {k}) for k in kids), default=0)

    return h(doc_id, frozenset({doc_id}))


def _siblings(
    docs: Sequence[Doc],
    parent_id: str | None,
    section_id: str | None,
    list_id: str | None,
    exclude: str | None = None,
) -> list[Doc]:
    if parent_id is not None:
        group = [d for d in docs if d.parent_id == parent_id]
    else:
        group = [
            d
            for d in docs
            if d.parent_id is None
            and d.list_id == list_id
            and (list_id is not None or d.section_id == section_id)
        ]
    group = [d for d in group if d.id != exclude]
    group.sort(key=lambda d: (d.position, d.created_at, d.id))
    return group


def body_ref(body: str, version_no: int) -> dict[str, int]:
    """What a page-save event records for a body: the page-history version that holds the text
    and its length, never the text itself (a page is up to MAX_BODY characters and is saved
    every few seconds while someone types; `GET /docs/{id}/versions/{n}` has the text). Saves
    by one author within FOLD_MINUTES fold into one version, which keeps the newest text."""
    return {"version_no": version_no, "chars": len(body)}


def _version(session: Session, doc: Doc) -> DocVersion | None:
    return session.scalar(
        select(DocVersion).where(
            DocVersion.doc_id == doc.id, DocVersion.version_no == doc.version_no
        )
    )


def _checkpoint(
    session: Session,
    cx: Change,
    doc: Doc,
    latest: DocVersion | None,
    *,
    new: bool,
    note: str | None = None,
) -> DocVersion:
    """Record the doc's current title and body in page history: fold into `latest` (the newest
    version, read before the change began: a query here would flush uncovered rows) when it is
    the same author's open sitting, else start a version. Returns the row for the caller's
    event to cover."""
    now = utcnow()
    if (
        not new
        and latest is not None
        and latest.author_id == cx.actor.account_id
        and latest.note is None
        and now - latest.updated_at < timedelta(minutes=FOLD_MINUTES)
    ):
        latest.title = doc.title
        latest.body_md = doc.body_md
        latest.updated_at = now
        return latest
    doc.version_no = (latest.version_no if latest else doc.version_no) + (1 if latest else 0)
    v = DocVersion(
        id=new_id(),
        doc_id=doc.id,
        version_no=doc.version_no,
        title=doc.title,
        body_md=doc.body_md,
        author_id=cx.actor.account_id,
        created_at=now,
        updated_at=now,
        note=note,
    )
    session.add(v)  # no query may run before the caller's event covers it
    return v


def replace_section(body: str, heading: str, content: str) -> str:
    """Replace what sits under `heading` (any level, case-insensitive) up to the next heading of
    the same or a higher level. A missing heading is added at the end as `## heading`."""
    lines = body.split("\n")
    want = " ".join(heading.lstrip("#").split()).lower()
    fenced = False
    for n, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING_RE.match(line)
        if m and " ".join(m.group(2).split()).lower() == want:
            level = len(m.group(1))
            end = len(lines)
            inner = False
            for k in range(n + 1, len(lines)):
                if lines[k].startswith("```"):
                    inner = not inner
                    continue
                mk = None if inner else _HEADING_RE.match(lines[k])
                if mk and len(mk.group(1)) <= level:
                    end = k
                    break
            block = ["", *content.strip("\n").split("\n"), ""] if content.strip() else [""]
            return "\n".join([*lines[: n + 1], *block, *lines[end:]]).rstrip("\n") + "\n"
    base = body.rstrip("\n")
    sep = "\n\n" if base else ""
    return f"{base}{sep}## {' '.join(heading.lstrip('#').split())}\n\n{content.strip()}\n"


def _append(body: str, text: str) -> str:
    base = body.rstrip("\n")
    return f"{base}\n\n{text.strip(chr(10))}\n" if base else f"{text.strip(chr(10))}\n"


# --- writes ---------------------------------------------------------------------------------


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


def _new_doc(actor: Actor, **cols: Any) -> tuple[Doc, DocVersion]:
    """A doc and its version 1, ids and times set so nothing needs a flush before the event."""
    now = utcnow()
    doc = Doc(
        id=new_id(),
        revision=1,
        version_no=1,
        created_by=actor.account_id,
        updated_by=actor.account_id,
        created_at=now,
        updated_at=now,
        **cols,
    )
    version = DocVersion(
        id=new_id(),
        doc_id=doc.id,
        version_no=1,
        title=doc.title,
        body_md=doc.body_md,
        author_id=actor.account_id,
        created_at=now,
        updated_at=now,
    )
    return doc, version


def _record_mentions(session: Session, cx: Change, doc: Doc, before: str, after: str) -> None:
    """`doc.mentioned` for the handles `after` @mentions that `before` did not."""
    old = set(mentions_in(session, before))
    added = guests.reachable_handles(
        session, cx.actor.account_id, [h for h in mentions_in(session, after) if h not in old]
    )
    if not added:
        return
    line = next((ln for ln in after.splitlines() if f"@{added[0]}" in ln.lower()), "")
    excerpt = " ".join(line.split())[:200]
    cx.record(
        "doc.mentioned",
        subject_id=doc.id,
        after={
            "doc_id": doc.id,
            "title": doc.title,
            "mentions": added,
            "excerpt": excerpt,
        },
    )
    from slate.service import docwake  # noqa: PLC0415 (docwake reads this module)

    docwake.on_mentioned(cx, session, doc, added, excerpt=excerpt)


def _insert(
    session: Session, cx: Change, doc: Doc, version: DocVersion, covers: Sequence[Any]
) -> Doc:
    cx.create(doc, "doc.created", covers=[version, *covers])
    session.add(version)
    return doc


def _renumber(ordered: Sequence[Doc]) -> list[Doc]:
    """Give siblings positions 0..n in order; returns the rows whose position changed."""
    changed = []
    for n, d in enumerate(ordered):
        if d.position != n:
            d.position = n
            changed.append(d)
    return changed


def create_doc(
    session: Session,
    actor: Actor,
    *,
    title: str | None = None,
    body_md: str | None = None,
    icon: str | None = None,
    parent_id: str | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    template: str | None = None,
    before_id: str | None = None,
    kind: str = "page",
    tags: Sequence[str] | None = None,
) -> Doc:
    """One page. Under `parent_id` it is a subpage (no location of its own); otherwise it sits
    in `list_id`, `section_id` or the workspace. `template` fills title, icon and body that
    the caller did not give. It goes last among its siblings, or before `before_id`.
    `kind = file` makes a file doc, empty until `add_revision`; its body is the description."""
    if kind not in KINDS:
        raise Invalid(f"kind must be one of {', '.join(KINDS)}")
    if kind != "page" and template not in (None, "blank"):
        raise Invalid(f"a {kind} takes no template")
    tpl = TEMPLATES.get(template or "blank")
    if tpl is None:
        raise Invalid(f"unknown template '{template}'; one of {', '.join(TEMPLATES)}")
    docs = _all(session)
    by_id = {d.id: d for d in docs}
    if parent_id is not None:
        parent = by_id.get(parent_id)
        if parent is None:
            raise Invalid(f"parent doc {parent_id} does not exist")
        if list_id is not None or section_id is not None:
            raise Invalid("a subpage lives where its top page lives; give parent_id or a place")
        if _depth(by_id, parent) >= MAX_DEPTH:
            raise Invalid(f"pages nest {MAX_DEPTH} deep at most")
        sec, lst = None, None
    else:
        sec, lst = _place(session, list_id, section_id)
    siblings = _siblings(docs, parent_id, sec, lst)
    doc, version = _new_doc(
        actor,
        kind=kind,
        title=_title(title if title is not None else tpl["title"]),
        icon=_icon(icon if icon is not None else tpl["icon"]),
        body_md=_body(body_md if body_md is not None else tpl["body_md"]),
        parent_id=parent_id,
        section_id=sec,
        list_id=lst,
        position=len(siblings),
    )
    if kind == "file":
        dated = created_from_name(doc.title)
        if dated is not None:
            doc.created_at = dated  # the date in the name is the file's date
    at = next((n for n, d in enumerate(siblings) if d.id == before_id), len(siblings))
    with apply_change(session, actor) as cx:
        moved = _renumber([*siblings[:at], doc, *siblings[at:]])
        _insert(session, cx, doc, version, [m for m in moved if m is not doc])
        _record_mentions(session, cx, doc, "", doc.body_md)
        if tags:
            session.flush()  # the doc row exists before its tags point at it
            _set_tags(cx, doc, tags)
    return doc


UPDATABLE = {
    "title",
    "icon",
    "body_md",
    "append_md",
    "replace_section",
    "base_revision",
    "new_version",
    "note",
    "tags",
}


def update_doc(session: Session, actor: Actor, doc_id: str, changes: Mapping[str, Any]) -> Doc:
    """Change a page's title, icon or body.

    `body_md` replaces the body; `replace_section` `{heading, body_md}` rewrites what sits under
    one heading (adding it when missing); `append_md` adds a paragraph at the end.
    `base_revision`: the revision the caller edited; a mismatch is a 409 and nothing changes.
    `new_version: true` (or a `note`) closes the current page-history version, named by `note`.
    """
    unknown = set(changes) - UPDATABLE
    if unknown:
        raise Invalid(f"cannot update {sorted(unknown)}")
    get_doc(session, doc_id)
    spec = changes.get("replace_section")
    heading = str(spec.get("heading") or "").strip() if spec else ""
    if spec and not heading:
        raise Invalid("replace_section needs a heading")
    note = str(changes.get("note") or "").strip()[:200] or None
    seal = bool(changes.get("new_version")) or note is not None
    tag_names = changes.get("tags")
    with apply_change(session, actor) as cx:
        # The check and the new body are worked out from the doc as it stands under the write
        # lock, so a save that committed since this request began is never overwritten unseen:
        # a stale `base_revision` is a 409 and two `append_md` calls both land.
        doc = fresh_doc(session, doc_id)
        latest = _version(session, doc)
        base = changes.get("base_revision")
        if base is not None and base != doc.revision:
            raise Conflict(
                f"doc changed since revision {base} (now {doc.revision}); reload it and reapply "
                "your edit"
            )
        wanted: dict[str, Any] = {}
        if changes.get("title") is not None:
            wanted["title"] = _title(changes["title"])
        if "icon" in changes:
            wanted["icon"] = _icon(changes["icon"])
        body = doc.body_md
        touched = False
        if changes.get("body_md") is not None:
            body, touched = _body(changes["body_md"]), True
        if spec:
            body, touched = replace_section(body, heading, str(spec.get("body_md") or "")), True
        if changes.get("append_md"):
            body, touched = _append(body, str(changes["append_md"])), True
        if touched:
            wanted["body_md"] = _body(body)
        diffs = {k: v for k, v in wanted.items() if getattr(doc, k) != v}
        if not diffs and not seal:
            if tag_names is not None:
                _set_tags(cx, doc, list(tag_names))
            return doc
        if tag_names is not None:
            _set_tags(cx, doc, list(tag_names))
        befores = {k: getattr(doc, k) for k in diffs}
        before_version = doc.version_no
        for k, v in diffs.items():
            setattr(doc, k, v)
        covers: list[Any] = [doc]
        if "title" in diffs or "body_md" in diffs or seal:
            if "title" in diffs or "body_md" in diffs:
                doc.revision += 1
            v = _checkpoint(session, cx, doc, latest, new=False)
            if seal:
                # Close this sitting: the next save starts a fresh version.
                v.note = note or v.note or "Saved version"
            covers.append(v)
        doc.updated_by = actor.account_id
        doc.updated_at = utcnow()
        for k in diffs:
            body = k == "body_md"
            cx.record(
                "doc.updated",
                subject_id=doc.id,
                field=k,
                before=body_ref(befores[k], before_version) if body else befores[k],
                after=body_ref(diffs[k], doc.version_no) if body else diffs[k],
                covers=covers,
            )
        if "body_md" in diffs:
            _record_mentions(session, cx, doc, befores["body_md"], diffs["body_md"])
        if not diffs:
            cx.record(
                "doc.updated",
                subject_id=doc.id,
                field="version_no",
                after={"version_no": doc.version_no, "note": covers[-1].note},
                covers=covers,
            )
    return doc


def _place_of(doc: Doc) -> dict[str, Any]:
    return {
        "parent_id": doc.parent_id,
        "section_id": doc.section_id,
        "list_id": doc.list_id,
        "position": doc.position,
    }


def move_doc(session: Session, actor: Actor, doc_id: str, to: Mapping[str, Any]) -> Doc:
    """Move a page (with its subpages) under another page (`parent_id`), or to the top level of
    a list, a section or the workspace (`list_id` / `section_id` / neither); before
    `before_id` among its new siblings, or last."""
    unknown = set(to) - {"parent_id", "list_id", "section_id", "before_id"}
    if unknown:
        raise Invalid(f"move does not take {sorted(unknown)}")
    access.check_object(session, get_doc(session, doc_id))  # edit where it is now, too
    parent_id = to.get("parent_id")
    if parent_id is not None:
        if to.get("list_id") is not None or to.get("section_id") is not None:
            raise Invalid("a subpage lives where its top page lives; give parent_id or a place")
        sec, lst = None, None
    else:
        sec, lst = _place(session, to.get("list_id"), to.get("section_id"))
    with apply_change(session, actor) as cx:
        # The tree is read and checked under the write lock (nothing is dirty yet, so the read
        # flushes nothing): a move that committed since this request began is seen, so two
        # moves at once cannot make a page its own ancestor or nest past MAX_DEPTH.
        docs = _all(session, fresh=True)
        by_id = {d.id: d for d in docs}
        doc = by_id[doc_id]
        if parent_id is not None:
            parent = by_id.get(parent_id)
            if parent is None:
                raise Invalid(f"parent doc {parent_id} does not exist")
            if parent_id == doc.id or doc in _ancestors(by_id, parent):
                raise Invalid("a page cannot move under itself or one of its subpages")
            if _depth(by_id, parent) + _height(docs, doc.id) > MAX_DEPTH:
                raise Invalid(f"pages nest {MAX_DEPTH} deep at most")
        before = _place_of(doc)
        siblings = _siblings(docs, parent_id, sec, lst, exclude=doc.id)
        before_id = to.get("before_id")
        at = next((n for n, d in enumerate(siblings) if d.id == before_id), len(siblings))
        doc.parent_id, doc.section_id, doc.list_id = parent_id, sec, lst
        changed = _renumber([*siblings[:at], doc, *siblings[at:]])
        if _place_of(doc) != before or changed:
            cx.record(
                "doc.moved",
                subject_id=doc.id,
                before=before,
                after=_place_of(doc),
                covers=[doc, *changed],
            )
    return doc


def archive_doc(session: Session, actor: Actor, doc_id: str) -> Doc:
    """Hide a page and everything under it (unarchive brings them back)."""
    doc = get_doc(session, doc_id)
    with apply_change(session, actor) as cx:
        cx.set(doc, "archived_at", doc.archived_at or utcnow(), "doc.archived")
    return doc


def unarchive_doc(session: Session, actor: Actor, doc_id: str) -> Doc:
    doc = get_doc(session, doc_id)
    with apply_change(session, actor) as cx:
        cx.set(doc, "archived_at", None, "doc.unarchived")
    return doc


def delete_doc(session: Session, actor: Actor, doc_id: str) -> list[str]:
    """Delete an archived page and its subpages for good (their versions and files go too;
    the events stay). Returns the deleted ids, subpages first."""
    doc = get_doc(session, doc_id)
    if doc.archived_at is None:
        raise Invalid("archive the doc first; only an archived doc can be deleted for good")
    docs = _all(session)
    doomed = [*reversed(_descendants(docs, doc.id)), doc]
    rows = {d.id: session.get(Doc, d.id) for d in doomed}
    for d in rows.values():
        d.body_md  # noqa: B018 - load the deferred body for the snapshot before the change
    with apply_change(session, actor) as cx:
        for d in doomed:
            cx.delete(rows[d.id], "doc.deleted")
            session.flush()  # subpages first: parent_id does not cascade
    return [d.id for d in doomed]


def restore_version(session: Session, actor: Actor, doc_id: str, version_no: int) -> Doc:
    """Put an older version's title and body back. Nothing is lost: the restore is itself a
    new version, and the text it replaced stays in the one before."""
    doc = get_doc(session, doc_id)
    old = get_version(session, doc_id, version_no)
    latest = _version(session, doc)
    before = {"title": doc.title, **body_ref(doc.body_md, doc.version_no)}
    with apply_change(session, actor) as cx:
        doc.title, doc.body_md = old.title, old.body_md
        doc.revision += 1
        doc.updated_by = actor.account_id
        doc.updated_at = utcnow()
        v = _checkpoint(session, cx, doc, latest, new=True, note=f"Restored version {version_no}")
        cx.record(
            "doc.version_restored",
            subject_id=doc.id,
            before=before,
            after={
                "title": doc.title,
                **body_ref(doc.body_md, doc.version_no),
                "restored_from": version_no,
            },
            covers=[doc, v],
        )
    return doc


def duplicate_doc(
    session: Session,
    actor: Actor,
    doc_id: str,
    *,
    title: str | None = None,
    subpages: bool = True,
) -> Doc:
    """Copy a page right after the original, with its subpages unless `subpages` is false.
    Each copy starts its own history at version 1. Only the copied docs are read, and a copied
    file keeps its revision's extracted text and tables (the bytes are the same), so nothing is
    parsed while the write lock is held."""
    src = get_doc(session, doc_id)
    docs = _all(session)
    siblings = _siblings(docs, src.parent_id, src.section_id, src.list_id)
    at = next(n for n, d in enumerate(siblings) if d.id == src.id) + 1

    # The docs to copy: the source and, with `subpages`, its live subtree (each doc once).
    kids_of: dict[str, list[Doc]] = {}
    for d in docs:
        if d.parent_id and d.archived_at is None:
            kids_of.setdefault(d.parent_id, []).append(d)
    copied: dict[str, Doc] = {src.id: src}
    stack = [src.id] if subpages else []
    while stack:
        for child in kids_of.get(stack.pop(), []):
            if child.id not in copied:
                copied[child.id] = child
                stack.append(child.id)
    full = {d_id: session.get(Doc, d_id) for d_id in copied}
    for d in full.values():
        d.body_md  # noqa: B018 - load bodies before the change (no queries inside it)

    current = current_revisions(session, list(copied.values()))
    if current:  # bytes, text and tables of the copied revisions, in one query, before the change
        session.scalars(
            select(DocFile)
            .options(undefer(DocFile.data), undefer(DocFile.text), undefer(DocFile.tables_json))
            .where(DocFile.id.in_([f.id for f in current.values()]))
        ).all()

    def clone(s: Doc, parent_id: str | None, name: str, position: int) -> tuple[Doc, DocVersion]:
        return _new_doc(
            actor,
            kind=s.kind,
            title=_title(name),
            icon=s.icon,
            body_md=s.body_md,
            parent_id=parent_id,
            section_id=s.section_id if parent_id is None else None,
            list_id=s.list_id if parent_id is None else None,
            position=position,
        )

    copy, version = clone(src, src.parent_id, title or f"{src.title} (copy)", at)

    def copy_revision(cx: Change, from_id: str, to: Doc) -> list[DocFile]:
        f = current.get(from_id)
        if f is None:
            return []
        rev = _revision_row(
            cx.actor,
            to.id,
            f.filename,
            f.data,
            f.content_type,
            1,
            f"Copied from {src.title}"[:200],
            extracted=(f.text, f.tables_json),
        )
        session.add(rev)
        return [rev]

    with apply_change(session, actor) as cx:
        moved = _renumber([*siblings[:at], copy, *siblings[at:]])
        _insert(session, cx, copy, version, [m for m in moved if m is not copy])
        for rev in copy_revision(cx, src.id, copy):
            cx.record(
                "doc.revision_added", subject_id=copy.id, after=revision_out(rev), covers=[rev]
            )
        if subpages:
            stack = [(src.id, copy.id)]
            while stack:
                from_id, to_id = stack.pop()
                kids = _tree_order(kids_of.get(from_id, []))
                for n, child in enumerate(kids):
                    dup, dv = clone(full[child.id], to_id, child.title, n)
                    _insert(session, cx, dup, dv, [])
                    for rev in copy_revision(cx, child.id, dup):
                        cx.record(
                            "doc.revision_added",
                            subject_id=dup.id,
                            after=revision_out(rev),
                            covers=[rev],
                        )
                    stack.append((child.id, dup.id))
    return copy


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


# --- drawers: a list of documents -------------------------------------------------------------


def pin_to_top(session: Session, actor: Actor, doc_id: str) -> Doc:
    """Pin a document (file, page or folder) to the top of its drawer or folder listing. Shared
    by everyone who sees the drawer; a second pin sits under the first. One `doc.pinned_to_top`;
    pinning what is pinned changes nothing."""
    doc = get_doc(session, doc_id)
    if doc.top_pinned_at is not None:
        return doc
    with apply_change(session, actor) as cx:
        doc.top_pinned_at = utcnow()
        doc.top_pinned_by = actor.account_id
        cx.record(
            "doc.pinned_to_top",
            subject_id=doc.id,
            field="top_pinned_at",
            before=None,
            after={"at": doc.top_pinned_at, "by": doc.top_pinned_by},
            covers=[doc],
        )
    return doc


def unpin_from_top(session: Session, actor: Actor, doc_id: str) -> Doc:
    doc = get_doc(session, doc_id)
    if doc.top_pinned_at is None:
        return doc
    before = {"at": doc.top_pinned_at, "by": doc.top_pinned_by}
    with apply_change(session, actor) as cx:
        doc.top_pinned_at = None
        doc.top_pinned_by = None
        cx.record(
            "doc.unpinned_from_top",
            subject_id=doc.id,
            field="top_pinned_at",
            before=before,
            after=None,
            covers=[doc],
        )
    return doc


def pinned_first(docs: Sequence[Doc]) -> list[Doc]:
    """Pinned entries first (oldest pin first), then the rest in the order given."""
    pinned = sorted(
        (d for d in docs if d.top_pinned_at is not None), key=lambda d: (d.top_pinned_at, d.id)
    )
    return pinned + [d for d in docs if d.top_pinned_at is None]


def drawer_listing(session: Session, list_id: str, folder_id: str | None = None) -> dict[str, Any]:
    """What a drawer shows: the entries at its top (or inside `folder_id`), pinned ones first,
    then tree order, each a summary plus `children_count`; the folder and its path; counts for
    the whole drawer."""
    from slate.service.doc_threads import counts as thread_counts

    lst = get_list(session, list_id)
    if lst.kind != "drawer":
        raise Invalid(f"'{lst.name}' is a task list, not a drawer")
    docs = list_docs(session, list_id=lst.id)
    by_id = {d.id: d for d in docs}
    folder = None
    path: list[Doc] = []
    if folder_id is not None:
        folder = by_id.get(folder_id)
        if folder is None or folder.kind != "folder":
            raise Invalid("that folder is not in this drawer")
        path = list(reversed(_ancestors(by_id, folder))) + [folder]
    entries = pinned_first(_tree_order([d for d in docs if d.parent_id == folder_id]))
    kids: dict[str, int] = {}
    for d in docs:
        if d.parent_id is not None:
            kids[d.parent_id] = kids.get(d.parent_id, 0) + 1
    rows = summaries(session, entries)
    for row in rows:
        row["children_count"] = kids.get(row["id"], 0)
    threads = thread_counts(session, [d.id for d in docs])
    return {
        "list": lst,
        "folder": summaries(session, [folder])[0] if folder is not None else None,
        "path": [_ref(d) for d in path],
        "entries": rows,
        "counts": {
            "docs": sum(1 for d in docs if d.kind != "folder"),
            "files": sum(1 for d in docs if d.kind == "file"),
            "folders": sum(1 for d in docs if d.kind == "folder"),
            "open_threads": sum(open_n for _, open_n in threads.values()),
        },
    }


def to_drawer(session: Session, actor: Actor, doc_id: str) -> List:
    """Turn a top-level page into a drawer of the same name in its section: a new drawer list,
    the page's subpages moved to the drawer's top (in order), then the page itself (its text is
    kept). A subpage, or a page in no section, is refused."""
    from slate.service import lists as lists_svc

    doc = get_doc(session, doc_id)
    if doc.parent_id is not None:
        raise Invalid("a subpage cannot become a drawer; use its top page")
    if doc.kind == "folder":
        raise Invalid("a folder cannot become a drawer")
    section_id = doc.section_id
    if section_id is None and doc.list_id is not None:
        section_id = get_list(session, doc.list_id).section_id
    if section_id is None:
        raise Invalid("a workspace page has no section to hold a drawer; move it first")
    docs = _all(session)
    children = _tree_order([d for d in docs if d.parent_id == doc.id and d.archived_at is None])
    with apply_change(session, actor):  # one change: a refused move leaves no empty drawer
        drawer = lists_svc.create_list(
            session, actor, section_id=section_id, name=doc.title, kind="drawer"
        )
        move_doc(session, actor, doc.id, {"list_id": drawer.id})
        for child in children:
            move_doc(session, actor, child.id, {"list_id": drawer.id})
    return drawer


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
    rev = _revision_row(actor, doc.id, filename, data, content_type, 0, text, restored_from)
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


def create_task(
    session: Session,
    actor: Actor,
    doc_id: str,
    *,
    title: str,
    list_id: str | None = None,
    replace: str | None = None,
    occurrence: int = 0,
    base_revision: int | None = None,
    **fields: Any,
) -> tuple[Item, Doc]:
    """File one task from a doc (ClickUp's "create task from selection"), in one change.

    The task goes to `list_id`, else the doc's list; its description ends with a link back to
    the page. With `replace`, that text (its `occurrence`-th appearance, counting from 0)
    becomes the task's `[[task:<id>]]` embed; without it the embed is added at the end.
    Exactly one item is created (AGENTS.md rule 1).
    """
    doc = get_doc(session, doc_id)
    target = list_id or root_of(session, doc).list_id
    if target is None:
        raise Invalid("list_id is required: this doc is not in a list")
    link = f"From doc [[doc:{doc.id}]]"
    desc = str(fields.pop("body_md", "") or "").strip()
    with apply_change(session, actor) as cx:
        # Checked against the doc as it stands under the write lock (see `update_doc`).
        doc = fresh_doc(session, doc_id)
        latest = _version(session, doc)
        if base_revision is not None and base_revision != doc.revision:
            raise Conflict(
                f"doc changed since revision {base_revision} (now {doc.revision}); reload it first"
            )
        body = doc.body_md
        at = -1
        if replace:
            for _ in range(occurrence + 1):
                at = body.find(replace, at + 1)
                if at < 0:
                    raise Invalid("the selected text is not in the doc (it may have changed)")
        item = items_svc.create_item(
            session,
            actor,
            list_id=target,
            title=title,
            body_md=f"{desc}\n\n{link}" if desc else link,
            **fields,
        )
        embed = f"[[task:{item.id}]]"
        new_body = (
            body[:at] + embed + body[at + len(replace) :] if replace else _append(body, embed)
        )
        before_version = doc.version_no
        doc.body_md = _body(new_body)
        doc.revision += 1
        doc.updated_by = actor.account_id
        doc.updated_at = utcnow()
        v = _checkpoint(session, cx, doc, latest, new=False)
        cx.record(
            "doc.updated",
            subject_id=doc.id,
            field="body_md",
            before=body_ref(body, before_version),
            after=body_ref(doc.body_md, doc.version_no),
            covers=[doc, v],
        )
    return item, doc


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
    rev = _revision_row(actor, "", att.filename, data, att.content_type, 1, text)
    with apply_change(session, actor):
        doc = create_doc(
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
    return get_doc(session, doc.id)
