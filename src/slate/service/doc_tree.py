"""The doc tree: reading it, walking it, and moving pages in it (part of `slate.service.docs`).

Every doc (page, file, folder) is a node of one tree. Only a top doc carries a location
(`list_id`, and `section_id` for quick filtering; neither = the workspace); docs under it inherit
its location. The walks here are the only ones: every other docs module and `doc_hub` use them.
Each walk stops on a broken link (a parent cycle) rather than looping.

Writes: `doc.moved` (before/after `{parent_id, section_id, list_id, position}`), `doc.archived`,
`doc.unarchived`, `doc.deleted`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, load_only, undefer

from slate.models import Doc, DocLink, List, Section, utcnow
from slate.service import access
from slate.service.core import Actor, apply_change
from slate.service.errors import Invalid, NotFound
from slate.service.lists import get_list
from slate.service.sections import get_section

MAX_DEPTH = 8  # a top page is depth 1
KINDS = ("page", "file", "folder")


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


def all_docs(session: Session, *, fresh: bool = False) -> list[Doc]:
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


def ancestors(by_id: Mapping[str, Doc], doc: Doc) -> list[Doc]:
    """Parent first, top page last. Stops on a broken link rather than looping."""
    out: list[Doc] = []
    seen = {doc.id}
    cur = by_id.get(doc.parent_id) if doc.parent_id else None
    while cur is not None and cur.id not in seen:
        out.append(cur)
        seen.add(cur.id)
        cur = by_id.get(cur.parent_id) if cur.parent_id else None
    return out


def top_of(by_id: Mapping[str, Doc], doc: Doc) -> Doc:
    """The top page above `doc` (itself when it is one): the doc that carries the location."""
    chain = ancestors(by_id, doc)
    return chain[-1] if chain else doc


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


def tree_order(docs: Sequence[Doc]) -> list[Doc]:
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


def _hidden_places(session: Session) -> tuple[set[str], set[str]]:
    lists = set(session.scalars(select(List.id).where(List.archived_at.is_not(None))))
    sections = set(session.scalars(select(Section.id).where(Section.archived_at.is_not(None))))
    return lists, sections


def live(session: Session, docs: Sequence[Doc], by_id: Mapping[str, Doc]) -> list[Doc]:
    """The docs a listing shows: not archived, under no archived page, and not in an archived
    list or section."""
    dead_lists, dead_sections = _hidden_places(session)

    def visible(d: Doc) -> bool:
        chain = ancestors(by_id, d)
        if d.archived_at is not None or any(a.archived_at is not None for a in chain):
            return False
        t = chain[-1] if chain else d
        return t.list_id not in dead_lists and t.section_id not in dead_sections

    return [d for d in docs if visible(d)]


def depth(by_id: Mapping[str, Doc], doc: Doc) -> int:
    return 1 + len(ancestors(by_id, doc))


def height(docs: Sequence[Doc], doc_id: str) -> int:
    """Levels in the subtree rooted at doc_id, counting itself."""
    children: dict[str, list[str]] = {}
    for d in docs:
        if d.parent_id:
            children.setdefault(d.parent_id, []).append(d.id)

    def h(i: str, seen: frozenset[str]) -> int:
        kids = [k for k in children.get(i, []) if k not in seen]
        return 1 + max((h(k, seen | {k}) for k in kids), default=0)

    return h(doc_id, frozenset({doc_id}))


def siblings(
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


def renumber(ordered: Sequence[Doc]) -> list[Doc]:
    """Give siblings positions 0..n in order; returns the rows whose position changed (for the
    caller's event to cover)."""
    changed = []
    for n, d in enumerate(ordered):
        if d.position != n:
            d.position = n
            changed.append(d)
    return changed


def place(
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


def _place_of(doc: Doc) -> dict[str, Any]:
    return {
        "parent_id": doc.parent_id,
        "section_id": doc.section_id,
        "list_id": doc.list_id,
        "position": doc.position,
    }


# --- writes ---------------------------------------------------------------------------------


def move_doc(session: Session, actor: Actor, doc_id: str, to: Mapping[str, Any]) -> Doc:
    """Move a page (with its subpages) under another page (`parent_id`), or to the top level of
    a list, a section or the workspace (`list_id` / `section_id` / neither); before
    `before_id` among its new siblings, or last."""
    unknown = set(to) - {"parent_id", "list_id", "section_id", "before_id"}
    if unknown:
        raise Invalid(f"move does not take {sorted(unknown)}")
    access.check_object(session, get_doc(session, doc_id))  # edit where it is now, too
    from slate.service import docs as docs_svc

    parent_id = to.get("parent_id")
    if parent_id is not None:
        if to.get("list_id") is not None or to.get("section_id") is not None:
            raise Invalid("a subpage lives where its top page lives; give parent_id or a place")
        sec, lst = None, None
    else:
        sec, lst = place(session, to.get("list_id"), to.get("section_id"))
    with docs_svc.apply_change(session, actor) as cx:
        # The tree is read and checked under the write lock (nothing is dirty yet, so the read
        # flushes nothing): a move that committed since this request began is seen, so two
        # moves at once cannot make a page its own ancestor or nest past MAX_DEPTH.
        docs = all_docs(session, fresh=True)
        by_id = {d.id: d for d in docs}
        doc = by_id[doc_id]
        if parent_id is not None:
            parent = by_id.get(parent_id)
            if parent is None:
                raise Invalid(f"parent doc {parent_id} does not exist")
            if parent_id == doc.id or doc in ancestors(by_id, parent):
                raise Invalid("a page cannot move under itself or one of its subpages")
            if depth(by_id, parent) + height(docs, doc.id) > docs_svc.MAX_DEPTH:
                raise Invalid(f"pages nest {MAX_DEPTH} deep at most")
        before = _place_of(doc)
        group = siblings(docs, parent_id, sec, lst, exclude=doc.id)
        before_id = to.get("before_id")
        at = next((n for n, d in enumerate(group) if d.id == before_id), len(group))
        doc.parent_id, doc.section_id, doc.list_id = parent_id, sec, lst
        changed = renumber([*group[:at], doc, *group[at:]])
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
    docs = all_docs(session)
    doomed = [*reversed(_descendants(docs, doc.id)), doc]
    rows = {d.id: session.get(Doc, d.id) for d in doomed}
    for d in rows.values():
        d.body_md  # noqa: B018 - load the deferred body for the snapshot before the change
    with apply_change(session, actor) as cx:
        for d in doomed:
            cx.delete(rows[d.id], "doc.deleted")
            session.flush()  # subpages first: parent_id does not cascade
            session.execute(delete(DocLink).where(DocLink.doc_id == d.id))
    return [d.id for d in doomed]
