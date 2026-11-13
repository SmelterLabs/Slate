"""The outward shape of a doc (part of `slate.service.docs`).

`doc_out` is the full payload a page read and every write route return: location, text, embeds,
outline, facts, children, backlinks and the file's revisions. Index rows live in `doc_listing`.

A page's links to tasks and other pages live in `doc_links` (one row per distinct embed), written
inside the same change that changes a body, so a page read answers "what links here" with an
indexed lookup instead of scanning every page body.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Doc, DocFile, DocLink, Item, List, Section
from slate.service import access
from slate.service.core import Change
from slate.service.doc_files import _file_rows, current_revisions, file_out, revision_out
from slate.service.doc_listing import _with_counts, doc_ref, doc_summary
from slate.service.doc_pages import embeds_in, outline, parse_facts
from slate.service.doc_tree import all_docs, ancestors, tree_order

EMBED_RE = re.compile(r"\[\[(task|doc):([0-9A-Za-z]{12})\]\]")


def sync_links(cx: Change, doc: Doc) -> None:
    """Make `doc_links` say exactly which tasks and pages this page embeds, covering the rows
    that change with `doc.links_updated` (one event, `before`/`after` = the two sets). No-op
    when the set is unchanged, and for a folder (folders have no body to embed in)."""
    session = cx.session
    with session.no_autoflush:
        rows = list(session.scalars(select(DocLink).where(DocLink.doc_id == doc.id)))
    wanted = (
        {(kind, ref) for kind, ref in EMBED_RE.findall(doc.body_md)}
        if doc.kind != "folder"
        else set()
    )
    have = {(r.target_kind, r.target_id) for r in rows}
    if have == wanted:
        return
    removed = [r for r in rows if (r.target_kind, r.target_id) not in wanted]
    added = [
        DocLink(doc_id=doc.id, target_kind=kind, target_id=ref)
        for kind, ref in sorted(wanted)
        if (kind, ref) not in have
    ]
    for r in removed:
        session.delete(r)
    for r in added:
        session.add(r)
    cx.record(
        "doc.links_updated",
        subject_id=doc.id,
        before=sorted(f"{k}:{v}" for k, v in have),
        after=sorted(f"{k}:{v}" for k, v in wanted),
        covers=[*removed, *added],
    )


def backlinks(session: Session, doc_id: str, docs: list[Doc] | None = None) -> list[dict[str, Any]]:
    """Live pages that embed this one, as refs. One indexed lookup; a page in an archived list
    or section, or under an archived page, is left out, the same as a listing would. `docs` is
    the tree the caller already loaded, so a page read walks it once."""
    ids = set(
        session.scalars(
            select(DocLink.doc_id).where(DocLink.target_kind == "doc", DocLink.target_id == doc_id)
        )
    )
    if not ids:
        return []
    if docs is None:
        docs = all_docs(session)
    by_id = {d.id: d for d in docs}
    with session.no_autoflush:
        hidden_lists = set(session.scalars(select(List.id).where(List.archived_at.is_not(None))))
        hidden_sections = set(
            session.scalars(select(Section.id).where(Section.archived_at.is_not(None)))
        )
    out = []
    for d in docs:
        if d.id not in ids:
            continue
        chain = ancestors(by_id, d)
        if d.archived_at is not None or any(a.archived_at is not None for a in chain):
            continue
        top = chain[-1] if chain else d
        if top.list_id in hidden_lists or top.section_id in hidden_sections:
            continue
        out.append(doc_ref(d))
    return access.scrub(session, out)


def doc_out(session: Session, doc: Doc) -> dict[str, Any]:
    """The whole page: body, path, outline, embedded tasks (as item views), linked pages,
    pages that link here, and uploaded files."""
    from slate.service import items as items_svc

    docs = all_docs(session)
    by_id = {d.id: d for d in docs}
    refs = embeds_in(doc.body_md)
    tasks = list(
        session.scalars(select(Item).where(Item.id.in_(refs["tasks"]), Item.trashed_at.is_(None)))
    )
    order = {ref: n for n, ref in enumerate(refs["tasks"])}
    tasks.sort(key=lambda i: order[i.id])
    linked = [by_id[ref] for ref in refs["docs"] if ref in by_id]
    rows = _file_rows(session, DocFile.doc_id == doc.id, order_by=DocFile.created_at)
    files = [f for f in rows if f.revision_no is None]
    revisions = sorted((f for f in rows if f.revision_no is not None), key=lambda f: -f.revision_no)
    kids = [
        d for d in tree_order([d for d in docs if d.parent_id == doc.id]) if d.archived_at is None
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
            {"id": a.id, "title": a.title, "icon": a.icon} for a in reversed(ancestors(by_id, doc))
        ],
        children=_with_counts(session, [doc_summary(d, kid_current.get(d.id)) for d in kids]),
        outline=outline(doc.body_md),
        facts=parse_facts(doc.body_md),
        tasks=items_svc.views(session, tasks),
        missing_tasks=[ref for ref in refs["tasks"] if ref not in {t.id for t in tasks}],
        docs=[doc_ref(d) for d in linked],
        backlinks=[
            {"id": d["id"], "title": d["title"], "icon": d["icon"]}
            for d in backlinks(session, doc.id, docs)
        ],
        files=[file_out(f) for f in files],
        revisions=[revision_out(f) for f in revisions],
        words=len(doc.body_md.split()),
    )
    return access.scrub(session, out)
