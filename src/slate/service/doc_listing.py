"""Finding and listing docs (part of `slate.service.docs`): filters and search, index rows,
tags, pins to the top of a listing, and drawers (a list of documents).

Writes: `doc.updated` (field `tags`), `doc.pinned_to_top`, `doc.unpinned_from_top`; `to_drawer`
creates a drawer list and moves pages into it in one change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from slate.db import contains
from slate.models import Doc, DocFile, DocLink, DocTag, List, Tag, utcnow
from slate.service import access
from slate.service import tags as tags_svc
from slate.service.core import Actor, Change, apply_change
from slate.service.doc_files import current_revisions, revision_out
from slate.service.doc_tree import (
    KINDS,
    all_docs,
    ancestors,
    get_doc,
    live,
    top_of,
    tree_order,
)
from slate.service.errors import Invalid
from slate.service.lists import get_list


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
    docs = all_docs(session)
    by_id = {d.id: d for d in docs}

    if archived:
        pool = [d for d in docs if d.archived_at is not None]
    else:
        pool = live(session, docs, by_id)
    if list_id is not None:
        pool = [d for d in pool if top_of(by_id, d).list_id == list_id]
    if section_id is not None:
        pool = [d for d in pool if top_of(by_id, d).section_id == section_id]
    if workspace:
        pool = [
            d
            for d in pool
            if top_of(by_id, d).section_id is None and top_of(by_id, d).list_id is None
        ]
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
                    or_(contains(DocFile.filename, q), contains(DocFile.text, q)),
                )
                .scalar_subquery()
            )
            stmt = stmt.where(
                or_(contains(Doc.title, q), contains(Doc.body_md, q), Doc.id.in_(named))
            )
        if embeds:
            stmt = stmt.where(
                Doc.id.in_(
                    select(DocLink.doc_id).where(
                        DocLink.target_id == embeds, DocLink.target_kind.in_(("task", "doc"))
                    )
                )
            )
        hits = set(session.scalars(stmt))
        pool = [d for d in pool if d.id in hits]
    ordered = tree_order(pool)
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
    docs = all_docs(session)
    by_id = {d.id: d for d in docs}

    def keep(d: Doc) -> bool:
        if list_id is not None and top_of(by_id, d).list_id != list_id:
            return False
        if section_id is not None and top_of(by_id, d).section_id != section_id:
            return False
        if include_archived:
            return True
        return d.archived_at is None and all(a.archived_at is None for a in ancestors(by_id, d))

    return tree_order([d for d in docs if keep(d)])


def snippet(body: str, q: str, width: int = 80) -> str | None:
    at = body.lower().find(q.lower())
    if at < 0:
        return None
    start = max(0, at - width // 2)
    end = min(len(body), at + len(q) + width // 2)
    text = " ".join(body[start:end].split())
    return ("…" if start else "") + text + ("…" if end < len(body) else "")


def doc_ref(d: Doc) -> dict[str, Any]:
    """A doc named in another payload: id, title, icon and kind."""
    return {"id": d.id, "title": d.title, "icon": d.icon, "kind": d.kind}


def kind_counts(docs: Sequence[Doc], threads: Mapping[str, tuple[int, int]]) -> dict[str, int]:
    """Documents (not folders), files and folders among `docs`, and their open review threads
    (`threads` is `doc_threads.counts` for them)."""
    return {
        "docs": sum(1 for d in docs if d.kind != "folder"),
        "files": sum(1 for d in docs if d.kind == "file"),
        "folders": sum(1 for d in docs if d.kind == "folder"),
        "open_threads": sum(threads.get(d.id, (0, 0))[1] for d in docs),
    }


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
    return access.scrub(session, rows)  # a guest's copy names no workspace agent


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
        path = list(reversed(ancestors(by_id, folder))) + [folder]
    entries = pinned_first(tree_order([d for d in docs if d.parent_id == folder_id]))
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
        "path": [doc_ref(d) for d in path],
        "entries": rows,
        "counts": kind_counts(docs, threads),
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
    docs = all_docs(session)
    children = tree_order([d for d in docs if d.parent_id == doc.id and d.archived_at is None])
    from slate.service import docs as docs_svc

    with docs_svc.apply_change(session, actor):  # one change: a refused move leaves no empty drawer
        drawer = lists_svc.create_list(
            session, actor, section_id=section_id, name=doc.title, kind="drawer"
        )
        docs_svc.move_doc(session, actor, doc.id, {"list_id": drawer.id})
        for child in children:
            docs_svc.move_doc(session, actor, child.id, {"list_id": drawer.id})
    return drawer
