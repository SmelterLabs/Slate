"""The project home's documents hub: for one section, its drawers with counts, its
section-level pages, and what needs the viewer's eyes.

- **drawers**: the section's drawer lists, each with `counts` (docs, files, folders, open
  review threads) and `needs_you` (how many of the entries below are in it).
- **pages**: the section's top-level pages that sit outside any drawer (Plan, Decisions).
- **needs_you**: open Asks on the section's docs addressed to the viewer (`kind: ask`), and open
  review threads someone else started on a doc the viewer has touched (edited, uploaded or
  commented on) (`kind: thread`). Each carries the doc, the comment and the board URL that
  opens the panel on that thread.
- **counts**: docs, files, open review threads across the section.

No feed: a project home says what exists and what needs you, not everything that happened.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from slate.models import DocComment, Event
from slate.service import access, doc_threads
from slate.service import docs as docs_svc
from slate.service import lists as lists_svc
from slate.service.doc_listing import doc_ref, kind_counts
from slate.service.doc_tree import top_of
from slate.service.errors import NotFound
from slate.service.sections import get_section


def section_documents(session: Session, section_id: str, me: str | None) -> dict[str, Any]:
    """`me` is the viewer's account id (what `needs_you` is about)."""
    get_section(session, section_id)
    caller = access.current(session)
    if caller is not None and caller.section_level(section_id) == 0:
        raise NotFound("section not found")
    docs = docs_svc.list_docs(session, section_id=section_id)
    ids = [d.id for d in docs]
    by_id = {d.id: d for d in docs}
    drawers = [
        lst for lst in lists_svc.list_lists(session, section_id=section_id) if lst.kind == "drawer"
    ]
    counts = doc_threads.counts(session, ids) if ids else {}

    needs: list[dict[str, Any]] = []
    if me is not None and ids:
        touched = (
            set(
                session.scalars(
                    select(DocComment.doc_id).where(
                        DocComment.doc_id.in_(ids), DocComment.author_id == me
                    )
                )
            )
            | {d.id for d in docs if d.updated_by == me or d.created_by == me}
            | set(
                session.scalars(
                    select(Event.subject_id).where(
                        Event.subject_id.in_(ids),
                        Event.actor_id == me,
                        Event.type.in_(
                            ("doc.updated", "doc.revision_added", "doc.version_restored")
                        ),
                    )
                )
            )
        )
        roots = session.scalars(
            select(DocComment)
            .where(
                DocComment.doc_id.in_(ids),
                DocComment.parent_id.is_(None),
                DocComment.resolved_at.is_(None),
                or_(
                    DocComment.assignee_id == me,
                    (DocComment.surface == "review") & (DocComment.author_id != me),
                ),
            )
            .order_by(DocComment.created_at)
        )
        for c in roots:
            if c.assignee_id == me:
                kind = "ask"
            elif c.surface == "review" and c.doc_id in touched:
                kind = "thread"
            else:
                continue
            doc = by_id[c.doc_id]
            needs.append(
                {
                    "kind": kind,
                    "doc": doc_ref(doc),
                    "list_id": top_of(by_id, doc).list_id,
                    "comment": doc_threads.views(session, doc, [c])[0],
                    "url": f"/#/docs/{doc.id}?comment={c.id}",
                }
            )

    out_drawers = []
    for lst in drawers:
        mine = [d for d in docs if top_of(by_id, d).list_id == lst.id]
        out_drawers.append(
            {
                "list": lst,
                "counts": kind_counts(mine, counts),
                "needs_you": sum(1 for n in needs if n["list_id"] == lst.id),
            }
        )
    pages = [d for d in docs if d.parent_id is None and d.list_id is None and d.kind != "folder"]
    return {
        "drawers": out_drawers,
        "pages": docs_svc.summaries(session, pages),
        "needs_you": needs,
        "counts": {k: v for k, v in kind_counts(docs, counts).items() if k != "folders"},
    }
