"""One search over tasks, docs and chat (`GET /search`, MCP `slate_search`). Reads only.

    search(session, "passport", limit=30) -> [{kind, id, title, snippet, where, at, ...}]

Three queries, each already access-filtered by the session's read criteria (service/access.py),
so a guest only finds what it can see: tasks through `items.query_items(q=)` (title and body),
docs through `docs.list_docs(q=)` (title, page text and the text inside files), and chat
messages through a `LIKE` over `messages.body_md`, the one query new here. In that pattern `%`
and `_` typed by the caller are literal (`escape="\\"`).

Hits come back tasks first, then docs, then messages, newest first within chat. Each group gets
an equal share of `limit`; what a short group leaves over goes to the next groups in order, so
the whole answer never exceeds `limit`. `where` names the place: the task's list, the doc's
list, drawer or section (or `Workspace`), the message's channel as a notification names it
(`#Todo`, `chat with My Agent`); a message hit also carries `channel` and `thread_id` so the board
can open the channel at that message.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.models import List, Message, Section
from slate.service import access, chat, docs
from slate.service import items as items_svc
from slate.service.errors import Invalid

DEFAULT_LIMIT = 30
MAX_LIMIT = 200
KINDS = ("task", "doc", "message")


def search(session: Session, q: str, *, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Typed hits for `q` (module docstring), at most `limit` of them."""
    q = (q or "").strip()
    if not q:
        raise Invalid("q is empty")
    limit = max(1, min(int(limit), MAX_LIMIT))
    groups = (_tasks(session, q, limit), _docs(session, q, limit), _messages(session, q, limit))
    return _share(limit, *groups)


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


def _tasks(session: Session, q: str, limit: int) -> list[dict[str, Any]]:
    rows = items_svc.query_items(session, q=q, limit=limit, order="updated")
    lists = _names(session, List, {i.list_id for i in rows})
    return [
        {
            "kind": "task",
            "id": i.id,
            "title": i.title,
            "snippet": docs.snippet(i.body_md or "", q) or docs.snippet(i.title, q),
            "where": lists.get(i.list_id),
            "at": i.updated_at,
            "list_id": i.list_id,
        }
        for i in rows
    ]


def _docs(session: Session, q: str, limit: int) -> list[dict[str, Any]]:
    rows = docs.list_docs(session, q=q, limit=limit)
    summaries = {s["id"]: s for s in docs.summaries(session, rows, q)}
    places = {d.id: access.doc_place(session, d) for d in rows}
    lists = _names(session, List, {lid for _, lid in places.values() if lid})
    sections = _names(session, Section, {sid for sid, _ in places.values() if sid})
    out = []
    for d in rows:
        section_id, list_id = places[d.id]
        where = (
            lists.get(list_id)
            if list_id
            else sections.get(section_id)
            if section_id
            else "Workspace"
        )
        out.append(
            {
                "kind": "doc",
                "id": d.id,
                "title": d.title,
                "snippet": summaries.get(d.id, {}).get("snippet") or docs.snippet(d.title, q),
                "where": where,
                "at": d.updated_at,
                "list_id": list_id,
            }
        )
    return out


def like_pattern(q: str) -> str:
    """`%q%` with the caller's `%`, `_` and `\\` escaped (use with `escape="\\"`)."""
    body = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{body}%"


def _messages(session: Session, q: str, limit: int) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(Message)
            .where(
                Message.deleted_at.is_(None),
                func.lower(Message.body_md).like(like_pattern(q), escape="\\"),
            )
            .order_by(Message.id.desc())
            .limit(limit)
        )
    )
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
            "snippet": docs.snippet(m.body_md, q),
            "where": chat.place(m.channel, names[m.channel]),
            "at": m.created_at,
            "channel": m.channel,
            "thread_id": m.parent_id,
        }
        for m in rows
    ]
