"""One search over tasks, docs and chat (`GET /search`, MCP `slate_search`). Reads only.

    search(session, "passport", limit=30) -> [{kind, id, title, snippet, where, at, ...}]

Three queries, each already access-filtered by the session's read criteria (service/access.py),
so a guest only finds what it can see:
- tasks: exact card ID (with or without `#`, even when closed or archived), title, description
  and comments (guest-filtered to hide workspace-agent comments); ranked exact ID first, then
  open, then closed, then archived;
- docs: exact doc ID, title, page text and the text inside files (`doc_pages.list_docs(q=)`);
- chat messages: `LIKE` over `messages.body_md` (deleted messages skipped).

All text queries match with `db.contains`: any case (Unicode, not only A-Z), and `%` and `_`
typed by the caller literal.

Hits come back tasks first, then docs, then messages, newest first within each tier. Each group
gets an equal share of `limit`; what a short group leaves over goes to the next groups in order,
so the whole answer never exceeds `limit`. Optional filters narrow to one `kind`, an `assignee`
(tasks only), a `list_id` or `section_id`, or toggle `include_closed` / `include_archived`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.db import contains
from slate.models import (
    Account,
    Comment,
    Item,
    ItemAssignee,
    List,
    Message,
    Section,
    Status,
    StatusKind,
)
from slate.service import access, chat, doc_pages, guests
from slate.service.errors import Invalid

DEFAULT_LIMIT = 30
MAX_LIMIT = 200
KINDS = ("task", "doc", "message")
_ID_RE = re.compile(r"^#?([0-9A-Za-z]{12})$")


def _exact_id(q: str) -> str | None:
    m = _ID_RE.match(q.strip())
    return m.group(1) if m else None


def search(
    session: Session,
    q: str,
    *,
    limit: int = DEFAULT_LIMIT,
    kind: str | None = None,
    assignee: str | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    include_closed: bool = True,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Typed hits for `q` (module docstring), at most `limit` of them."""
    q = (q or "").strip()
    if not q:
        raise Invalid("q is empty")
    if kind is not None and kind not in KINDS:
        raise Invalid(f"kind must be one of {', '.join(KINDS)}")
    limit = max(1, min(int(limit), MAX_LIMIT))
    want_tasks = kind in (None, "task")
    want_docs = kind in (None, "doc") and assignee is None
    want_messages = kind in (None, "message") and assignee is None
    groups: list[list[dict[str, Any]]] = []
    if want_tasks:
        groups.append(
            _tasks(
                session,
                q,
                limit,
                assignee=assignee,
                list_id=list_id,
                section_id=section_id,
                include_closed=include_closed,
                include_archived=include_archived,
            )
        )
    if want_docs:
        groups.append(_docs(session, q, limit, list_id=list_id, section_id=section_id))
    if want_messages:
        groups.append(_messages(session, q, limit, list_id=list_id, section_id=section_id))
    return _share(limit, *groups) if groups else []


def _share(limit: int, *groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Equal shares first, then the leftovers in group order."""
    take = [min(len(g), limit // len(groups)) for g in groups]
    left = limit - sum(take)
    for i, g in enumerate(groups):
        extra = min(len(g) - take[i], left)
        take[i] += extra
        left -= extra
    return [hit for g, n in zip(groups, take, strict=True) for hit in g[:n]]


def _names(session: Session, model: Any, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    return dict(session.execute(select(model.id, model.name).where(model.id.in_(ids))).all())


def _visible_comment_matches(session: Session, q: str) -> Any:
    stmt = select(Comment.item_id).where(contains(Comment.body_md, q))
    if guests.viewer_is_guest(session):
        ws = guests.workspace_agent_ids(session)
        if ws:
            stmt = stmt.where(
                Comment.author_id.not_in(ws),
                or_(Comment.assignee_id.is_(None), Comment.assignee_id.not_in(ws)),
            )
    return stmt


def _tasks(
    session: Session,
    q: str,
    limit: int,
    *,
    assignee: str | None = None,
    list_id: str | None = None,
    section_id: str | None = None,
    include_closed: bool = True,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    bare_id = _exact_id(q)
    comment_items = _visible_comment_matches(session, q)
    text_match = or_(
        contains(Item.title, q),
        contains(Item.body_md, q),
        Item.id.in_(comment_items),
    )

    stmt = (
        select(Item, List, Section, Status)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(Item.trashed_at.is_(None))
    )
    if bare_id:
        id_match = func.lower(Item.id) == bare_id.lower()
        stmt = stmt.where(or_(id_match, text_match))
    else:
        stmt = stmt.where(text_match)

    if list_id is not None:
        stmt = stmt.where(Item.list_id == list_id)
    if section_id is not None:
        stmt = stmt.where(List.section_id == section_id)
    if assignee is not None:
        account_ids = select(Account.id).where(
            or_(Account.id == assignee, Account.handle == assignee.strip().lower())
        )
        stmt = stmt.where(
            Item.id.in_(
                select(ItemAssignee.item_id).where(ItemAssignee.account_id.in_(account_ids))
            )
        )

    if not include_archived:
        unarchived = (
            Item.archived_at.is_(None) & List.archived_at.is_(None) & Section.archived_at.is_(None)
        )
        stmt = stmt.where(
            or_(func.lower(Item.id) == bare_id.lower(), unarchived) if bare_id else unarchived
        )
    if not include_closed:
        open_cond = or_(
            Status.kind.is_(None),
            Status.kind.not_in([StatusKind.done, StatusKind.cancelled]),
        )
        stmt = stmt.where(
            or_(func.lower(Item.id) == bare_id.lower(), open_cond) if bare_id else open_cond
        )

    rows = list(session.execute(stmt.order_by(Item.updated_at.desc(), Item.id.desc())).all())
    if not rows:
        return []

    item_ids = [item.id for item, _, _, _ in rows]
    assignees_by_item: dict[str, list[str]] = defaultdict(list)
    for iid, aid in session.execute(
        select(ItemAssignee.item_id, ItemAssignee.account_id).where(
            ItemAssignee.item_id.in_(item_ids)
        )
    ):
        assignees_by_item[iid].append(aid)

    # Only pull comment snippets for items whose title/body did not already match.
    need_comment_snippet: list[str] = []
    q_low = q.lower()
    for item, _, _, _ in rows:
        is_id = bool(bare_id and item.id.lower() == bare_id.lower())
        if (
            not is_id
            and q_low not in item.title.lower()
            and q_low not in (item.body_md or "").lower()
        ):
            need_comment_snippet.append(item.id)
    comment_snippets: dict[str, str] = {}
    if need_comment_snippet:
        c_stmt = (
            select(Comment.item_id, Comment.body_md)
            .where(Comment.item_id.in_(need_comment_snippet), contains(Comment.body_md, q))
            .order_by(Comment.created_at.desc(), Comment.id.desc())
        )
        if guests.viewer_is_guest(session):
            ws = guests.workspace_agent_ids(session)
            if ws:
                c_stmt = c_stmt.where(
                    Comment.author_id.not_in(ws),
                    or_(Comment.assignee_id.is_(None), Comment.assignee_id.not_in(ws)),
                )
        for iid, body in session.execute(c_stmt):
            if iid not in comment_snippets:
                comment_snippets[iid] = doc_pages.snippet(body, q) or ""

    ranked: list[tuple[tuple[int, float], dict[str, Any]]] = []
    for item, lst, sec, st in rows:
        is_id = bool(bare_id and item.id.lower() == bare_id.lower())
        is_archived = bool(item.archived_at or lst.archived_at or sec.archived_at)
        is_closed = bool(st is not None and st.kind in (StatusKind.done, StatusKind.cancelled))
        if is_id:
            matched_in = "id"
            snippet = doc_pages.snippet(item.body_md or "", q) or doc_pages.snippet(item.title, q)
        elif q_low in (item.body_md or "").lower():
            matched_in = "body"
            snippet = doc_pages.snippet(item.body_md or "", q)
        elif q_low in item.title.lower():
            matched_in = "title"
            snippet = doc_pages.snippet(item.title, q)
        elif item.id in comment_snippets:
            matched_in = "comment"
            snippet = comment_snippets[item.id] or None
        else:
            matched_in = "title"
            snippet = doc_pages.snippet(item.title, q)

        tier = 0 if is_id else (3 if is_archived else (2 if is_closed else 1))
        ranked.append(
            (
                (tier, -item.updated_at.timestamp()),
                {
                    "kind": "task",
                    "id": item.id,
                    "title": item.title,
                    "snippet": snippet,
                    "where": lst.name,
                    "section": sec.name,
                    "at": item.updated_at,
                    "list_id": item.list_id,
                    "status_name": st.name if st else None,
                    "status_kind": st.kind.value if st else None,
                    "status_color": st.color if st else None,
                    "archived": is_archived,
                    "assignees": assignees_by_item.get(item.id, []),
                    "matched_in": matched_in,
                },
            )
        )
    ranked.sort(key=lambda pair: pair[0])
    return [hit for _, hit in ranked[:limit]]


def _docs(
    session: Session,
    q: str,
    limit: int,
    *,
    list_id: str | None = None,
    section_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = doc_pages.list_docs(session, q=q, limit=limit, list_id=list_id, section_id=section_id)
    bare_id = _exact_id(q)
    if bare_id and not any(d.id.lower() == bare_id.lower() for d in rows):
        from slate.models import Doc

        exact = session.scalar(
            select(Doc).where(func.lower(Doc.id) == bare_id.lower(), Doc.archived_at.is_(None))
        )
        if exact is not None:
            sid, lid = access.doc_place(session, exact)
            if (list_id is None or lid == list_id) and (section_id is None or sid == section_id):
                rows = [exact, *rows][:limit]
    summaries = {s["id"]: s for s in doc_pages.summaries(session, rows, q)}
    places = {d.id: access.doc_place(session, d) for d in rows}
    lists = _names(session, List, {lid for _, lid in places.values() if lid})
    sections = _names(session, Section, {sid for sid, _ in places.values() if sid})
    out = []
    for d in rows:
        sid, lid = places[d.id]
        where = lists.get(lid) if lid else sections.get(sid) if sid else "Workspace"
        out.append(
            {
                "kind": "doc",
                "id": d.id,
                "title": d.title,
                "snippet": summaries.get(d.id, {}).get("snippet") or doc_pages.snippet(d.title, q),
                "where": where,
                "section": sections.get(sid) if sid else None,
                "at": d.updated_at,
                "list_id": lid,
                "matched_in": "id" if bare_id and d.id.lower() == bare_id.lower() else None,
            }
        )
    return out


def _messages(
    session: Session,
    q: str,
    limit: int,
    *,
    list_id: str | None = None,
    section_id: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(Message).where(Message.deleted_at.is_(None), contains(Message.body_md, q))
    if list_id is not None:
        stmt = stmt.where(Message.channel == f"list:{list_id}")
    elif section_id is not None:
        lids = list(session.scalars(select(List.id).where(List.section_id == section_id)))
        channels = [f"section:{section_id}", *(f"list:{lid}" for lid in lids)]
        stmt = stmt.where(Message.channel.in_(channels))
    rows = list(session.scalars(stmt.order_by(Message.id.desc()).limit(limit)))
    names: dict[str, str | None] = {}
    for m in rows:
        if m.channel not in names:
            try:
                names[m.channel] = chat.parse_channel(session, m.channel).name
            except Exception:  # noqa: BLE001 - a channel whose list is gone: show the key
                names[m.channel] = None
    return [
        {
            "kind": "message",
            "id": m.id,
            "title": names[m.channel] or m.channel,
            "snippet": doc_pages.snippet(m.body_md, q),
            "where": chat.place(m.channel, names[m.channel]),
            "at": m.created_at,
            "channel": m.channel,
            "thread_id": m.parent_id,
        }
        for m in rows
    ]
