"""Exports: a list, a section or everything as JSON (complete) or CSV (one row per task), and
a consistent copy of the whole database for admins. All reads; nothing here writes.

JSON (`export_json`) is meant to be read back by a program: `{format: "slate-export",
version: 1, exported_at, scope, accounts, sections, lists, tags, items, comments,
checklists, checklist_items, links, docs, messages, message_reactions}`. Items
are the API's item shape (the same as `GET /items`); the related rows are their columns; docs
are pages with their bodies. Archived items are included unless asked not to; trashed items
never are. Chat comes with its scope: a list's channel, a section's channel and its lists'
channels, or every channel (General too) for everything; thread replies are messages with a
`parent_id`. Read cursors are not exported.

CSV (`export_csv`) is meant for a spreadsheet: one row per task (subtasks too, with
`parent_id`), names instead of ids, times in ISO 8601 UTC.

The backup (`backup_to`) uses SQLite's online backup API, so it is consistent while Slate keeps
writing; it holds everything, including token hashes and encrypted secrets, which is why only
admins can download it.

An agent is refused all three when the workspace denies agents `export`
(`service/agent_rules.py`).
"""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from slate.ids import slugify
from slate.models import (
    Account,
    Checklist,
    ChecklistItem,
    Comment,
    Item,
    ItemLink,
    List,
    Message,
    MessageReaction,
    Section,
    utcnow,
)
from slate.service import access, agent_rules
from slate.service import docs as docs_svc
from slate.service import items as items_svc
from slate.service import lists as lists_svc
from slate.service import sections as sections_svc
from slate.service import tags as tags_svc
from slate.service.core import jsonable, snapshot

FORMAT = "slate-export"
VERSION = 1
CSV_COLUMNS = (
    "id",
    "title",
    "section",
    "list",
    "parent_id",
    "status",
    "status_kind",
    "priority",
    "assignees",
    "tags",
    "start_at",
    "due_at",
    "completed_at",
    "estimate_minutes",
    "waiting_on",
    "created_at",
    "created_by",
    "archived_at",
    "external_ref",
    "description",
)


def scope_of(
    session: Session, *, list_id: str | None = None, section_id: str | None = None
) -> dict[str, Any]:
    """`{kind, id, name, slug}` of what is being exported (validates the id)."""
    if list_id is not None:
        lst = lists_svc.get_list(session, list_id)
        return {"kind": "list", "id": lst.id, "name": lst.name, "slug": lst.slug}
    if section_id is not None:
        sec = sections_svc.get_section(session, section_id)
        return {"kind": "section", "id": sec.id, "name": sec.name, "slug": sec.slug}
    return {"kind": "everything", "id": None, "name": "Everything", "slug": "everything"}


def filename(scope: dict[str, Any], ext: str, now: datetime | None = None) -> str:
    day = (now or utcnow()).strftime("%Y-%m-%d")
    return f"slate-{slugify(scope['slug']) or scope['kind']}-{day}.{ext}"


def _items(
    session: Session, list_id: str | None, section_id: str | None, include_archived: bool
) -> list:
    return items_svc.query_items(
        session, list_id=list_id, section_id=section_id, include_archived=include_archived
    )


def export_json(
    session: Session,
    *,
    list_id: str | None = None,
    section_id: str | None = None,
    include_archived: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Everything about the tasks in scope, as plain JSON-ready data."""
    agent_rules.check_read(session, "export")
    scope = scope_of(session, list_id=list_id, section_id=section_id)
    rows = _items(session, list_id, section_id, include_archived)
    ids = [i.id for i in rows]
    list_ids = {i.list_id for i in rows}
    if list_id is not None:
        list_ids.add(list_id)
    if section_id is not None:
        list_ids |= set(session.scalars(select(List.id).where(List.section_id == section_id)))
    if scope["kind"] == "everything":
        list_ids |= set(session.scalars(select(List.id)))
    lists = list(session.scalars(select(List).where(List.id.in_(list_ids)).order_by(List.name)))
    section_ids = {lst.section_id for lst in lists}
    sections = list(
        session.scalars(
            select(Section).where(Section.id.in_(section_ids)).order_by(Section.position)
        )
    )

    def statuses_of(lid: str) -> list:
        return lists_svc.statuses_of(session, lid)

    def by_item(model: Any, *order: Any) -> list[dict[str, Any]]:
        if not ids:
            return []
        stmt = select(model).where(model.item_id.in_(ids)).order_by(*order)
        return [snapshot(r) for r in session.scalars(stmt)]

    link_rows = (
        list(
            session.scalars(
                select(ItemLink).where(
                    or_(ItemLink.from_item_id.in_(ids), ItemLink.to_item_id.in_(ids))
                )
            )
        )
        if ids
        else []
    )
    # A link whose other end the caller cannot read is left out, as the item views leave it.
    ends = {r.from_item_id for r in link_rows} | {r.to_item_id for r in link_rows}
    readable = set(session.scalars(select(Item.id).where(Item.id.in_(ends)))) if ends else set()
    links = [
        snapshot(r) for r in link_rows if r.from_item_id in readable and r.to_item_id in readable
    ]
    if scope["kind"] == "everything":
        in_channels: Any = true()
    else:
        keys = [f"list:{lid}" for lid in list_ids]
        if section_id is not None:
            keys.append(f"section:{section_id}")
        in_channels = Message.channel.in_(keys)
    messages = list(
        session.scalars(select(Message).where(in_channels).order_by(Message.channel, Message.id))
    )
    message_ids = [m.id for m in messages]
    reactions = (
        session.scalars(
            select(MessageReaction)
            .where(MessageReaction.message_id.in_(message_ids))
            .order_by(MessageReaction.message_id, MessageReaction.created_at)
        )
        if message_ids
        else []
    )
    visible = access.participant_ids(session)
    caller = access.current(session)
    docs_in_scope = docs_svc.in_scope(
        session, list_id=list_id, section_id=section_id, include_archived=include_archived
    )
    current = docs_svc.current_revisions(session, docs_in_scope)
    docs_rows = [
        {
            **snapshot(d),
            "file": docs_svc.revision_out(current[d.id]) if d.id in current else None,
        }
        for d in docs_in_scope
    ]
    # A guest's copy names no workspace agent (raw snapshots carry account ids).
    body = jsonable(
        {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": now or utcnow(),
            "scope": {k: v for k, v in scope.items() if k != "slug"},
            "accounts": [
                {"id": a.id, "handle": a.handle, "display_name": a.display_name, "kind": a.kind}
                for a in session.scalars(select(Account).order_by(Account.handle))
                if visible is None or a.id in visible
            ],
            "sections": [
                {
                    k: v
                    for k, v in snapshot(s).items()
                    if k != "whiteboard_url" or caller is None or caller.section_level(s.id) > 0
                }
                for s in sections
            ],
            "lists": [
                {
                    **snapshot(lst),
                    "statuses": [snapshot(s) for s in lists_svc.statuses_of(session, lst.id)],
                }
                for lst in lists
            ],
            "tags": [snapshot(t) for t in tags_svc.list_tags(session)],
            "items": items_svc.views(session, rows),
            "comments": by_item(Comment, Comment.created_at),
            "checklists": by_item(Checklist, Checklist.position),
            "checklist_items": by_item(ChecklistItem, ChecklistItem.position),
            "links": links,
            "docs": docs_rows,
            "messages": [snapshot(m) for m in messages],
            "message_reactions": [snapshot(r) for r in reactions],
        }
    )
    return access.scrub(session, body)


def export_csv(
    session: Session,
    *,
    list_id: str | None = None,
    section_id: str | None = None,
    include_archived: bool = True,
) -> str:
    """One row per task in scope (CSV_COLUMNS), for a spreadsheet."""
    agent_rules.check_read(session, "export")
    scope_of(session, list_id=list_id, section_id=section_id)
    rows = _items(session, list_id, section_id, include_archived)
    views = items_svc.views(session, rows)
    lists = {lst.id: lst for lst in session.scalars(select(List))}
    sections = {s.id: s.name for s in session.scalars(select(Section))}
    handles = dict(session.execute(select(Account.id, Account.handle)).all())
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for v in views:
        lst = lists.get(v["list_id"])
        status = v["status"] or {}
        writer.writerow(
            csv_cell(c)
            for c in [
                v["id"],
                v["title"],
                sections.get(lst.section_id, "") if lst else "",
                lst.name if lst else "",
                v["parent_id"] or "",
                status.get("name", ""),
                status.get("kind", ""),
                v["priority"] or "",
                "; ".join(a["handle"] for a in v["assignees"]),
                "; ".join(v["tags"]),
                _iso(v["start_at"]),
                _iso(v["due_at"]),
                _iso(v["completed_at"]),
                v["estimate_minutes"] if v["estimate_minutes"] is not None else "",
                handles.get(v["waiting_on_account_id"], "") if v["waiting_on_account_id"] else "",
                _iso(v["created_at"]),
                handles.get(v["created_by"], v["created_by"]),
                _iso(v["archived_at"]),
                v["external_ref"] or "",
                v["body_md"],
            ]
        )
    return out.getvalue()


# A cell starting with one of these is a formula to Excel and Sheets (OWASP "CSV injection").
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: object) -> object:
    """A CSV cell a spreadsheet shows as text: a leading `'` defuses a formula. Dates (which
    start with a digit) and numbers pass unchanged."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _iso(value: datetime | None) -> str:
    return jsonable(value) if value is not None else ""


def backup_to(session: Session, dest: Path) -> int:
    """Copy the whole database into `dest` (a new SQLite file). Returns its size in bytes."""
    agent_rules.check_read(session, "export")
    source = session.connection().connection.driver_connection
    assert isinstance(source, sqlite3.Connection)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
    return dest.stat().st_size
