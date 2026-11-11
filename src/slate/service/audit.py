"""The audit log: every event, newest first, with who did it (person or agent), through which
door, and what it touched, filterable and exportable as CSV.

It reads the same append-only `events` table as everything else, through the caller's
permissions (service/access.py): an owner or admin sees the whole log; anyone else sees
events on the lists they can read plus their own actions.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, Event, Item, List, Section, Source
from slate.service.access import event_snapshots
from slate.service.core import jsonable
from slate.service.errors import Invalid

MAX_PAGE = 500
EXPORT_MAX = 50_000


@dataclass(frozen=True)
class AuditFilter:
    actor: str | None = None  # handle or id
    actor_kind: str | None = None  # person | agent
    source: str | None = None  # ui | api | mcp | system
    types: tuple[str, ...] = ()  # exact types or prefixes ending in "." (e.g. "share.")
    item_id: str | None = None
    list_id: str | None = None
    section_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    q: str | None = None  # text in the item title or session_ref


def _query(session: Session, f: AuditFilter) -> Any:
    q = select(Event)
    if f.actor:
        ref = f.actor.strip()
        q = q.where(
            Event.actor_id.in_(
                select(Account.id).where(or_(Account.id == ref, Account.handle == ref.lower()))
            )
        )
    if f.actor_kind:
        try:
            kind = AccountKind(f.actor_kind)
        except ValueError:
            raise Invalid("actor_kind must be person or agent") from None
        q = q.where(Event.actor_id.in_(select(Account.id).where(Account.kind == kind)))
    if f.source:
        try:
            q = q.where(Event.source == Source(f.source))
        except ValueError:
            raise Invalid("source must be ui, api, mcp or system") from None
    if f.types:
        exact = [t for t in f.types if not t.endswith(".")]
        prefixes = [t for t in f.types if t.endswith(".")]
        conds = [Event.type.in_(exact)] if exact else []
        conds += [Event.type.startswith(p) for p in prefixes]
        q = q.where(or_(*conds))
    if f.item_id:
        q = q.where(Event.item_id == f.item_id)
    if f.list_id or f.section_id:
        lists: Any = (
            [f.list_id] if f.list_id else select(List.id).where(List.section_id == f.section_id)
        )
        q = q.where(
            or_(
                Event.item_id.in_(select(Item.id).where(Item.list_id.in_(lists))),
                Event.subject_id.in_(lists),
                Event.subject_id == (f.section_id or f.list_id),
            )
        )
    if f.since:
        q = q.where(Event.at >= f.since)
    if f.until:
        q = q.where(Event.at < f.until)
    if f.q:
        needle = f"%{f.q.strip()}%"
        q = q.where(
            or_(
                Event.item_id.in_(select(Item.id).where(Item.title.ilike(needle))),
                Event.session_ref.ilike(needle),
                Event.type.ilike(needle),
            )
        )
    return q


VERBS = {
    "item.created": "created",
    "item.updated": "changed {field} on",
    "item.status_changed": "changed the status of",
    "item.assigned": "changed who is on",
    "item.commented": "commented on",
    "item.mentioned": "mentioned someone on",
    "item.archived": "archived",
    "item.unarchived": "unarchived",
    "item.trashed": "moved to the trash",
    "item.restored": "restored",
    "item.purged": "deleted for good",
    "item.deleted": "deleted",
    "item.moved": "moved",
    "item.split": "split",
    "item.reported": "reported on",
    "comment.edited": "edited a comment on",
    "comment.deleted": "deleted a comment on",
    "share.granted": "shared {scope} with {who}",
    "share.changed": "changed {who}'s access to {scope}",
    "share.revoked": "stopped sharing {scope} with {who}",
    "account.role_changed": "made {subject} {after}",
    "account.created": "added {subject}",
    "account.updated": "renamed {subject}",
    "account.disabled": "disabled {subject}",
    "account.enabled": "enabled {subject}",
    "token.created": "issued a token",
    "settings.changed": "changed what agents may not do",
    "token.revoked": "revoked a token",
    "list.created": "created the list {scope}",
    "list.updated": "changed {field} of the list {scope}",
    "list.archived": "archived the list {scope}",
    "section.created": "created the section {scope}",
    "section.updated": "changed {field} of the section {scope}",
    "section.archived": "archived the section {scope}",
    "message.posted": "posted in chat",
    "message.edited": "edited a chat message",
    "message.deleted": "deleted a chat message",
}


@dataclass
class _Names:
    accounts: dict[str, str]
    scopes: dict[str, str]


def _describe(ev: Event, item: dict | None, names: _Names) -> str:
    """One short line in words: "changed title on “Buy milk”", "shared Work with alex"."""
    row = ev.after_json if isinstance(ev.after_json, dict) else (ev.before_json or {})
    if not isinstance(row, dict):
        row = {}
    scope_id = row.get("scope_id") or ev.subject_id
    if ev.field == "private" and ev.type in ("list.updated", "section.updated"):
        kind = ev.type.split(".")[0]
        name = names.scopes.get(ev.subject_id or "", f"a {kind}")
        return f"made the {kind} {name} {'private' if ev.after_json else 'public'}"
    verb = VERBS.get(ev.type)
    if verb is None:
        verb = ev.type.replace(".", " ").replace("_", " ")
        if ev.field:
            verb = f"{verb} ({ev.field})"
    else:
        verb = verb.format(
            field=(ev.field or "something").replace("_", " "),
            scope=names.scopes.get(scope_id or "", "a list"),
            who=names.accounts.get(row.get("account_id") or "", "someone"),
            subject=names.accounts.get(ev.subject_id or "", "an account"),
            after=ev.after_json if isinstance(ev.after_json, str) else "",
        ).strip()
    if item:
        return f"{verb} “{item['title']}”"
    return verb


def _rows(session: Session, events: list[Event]) -> list[dict[str, Any]]:
    actor_ids = {e.actor_id for e in events}
    actors = {
        a.id: {"id": a.id, "handle": a.handle, "display_name": a.display_name, "kind": a.kind}
        for a in session.scalars(select(Account).where(Account.id.in_(actor_ids)))
    }
    item_ids = {e.item_id for e in events if e.item_id}
    items = {
        i.id: {"id": i.id, "title": i.title, "list_id": i.list_id}
        for i in session.scalars(select(Item).where(Item.id.in_(item_ids)))
    }
    scopes = dict(session.execute(select(List.id, List.name)).all())
    scopes.update(dict(session.execute(select(Section.id, Section.name)).all()))
    for i in items.values():
        i["list_name"] = scopes.get(i["list_id"])
    names = _Names(
        accounts={a.id: a.handle for a in session.scalars(select(Account))}, scopes=scopes
    )
    out = []
    for ev in events:
        item = items.get(ev.item_id) if ev.item_id else None
        before, after = event_snapshots(session, ev)
        out.append(
            {
                "id": ev.id,
                "at": ev.at,
                "type": ev.type,
                "field": ev.field,
                "before": before,
                "after": after,
                "source": jsonable(ev.source),
                "session_ref": ev.session_ref,
                "actor": actors.get(ev.actor_id)
                or {"id": ev.actor_id, "handle": None, "display_name": None, "kind": None},
                "item_id": ev.item_id,
                "subject_id": ev.subject_id,
                "item": item,
                "summary": _describe(ev, item, names),
            }
        )
    return out


def page(
    session: Session, f: AuditFilter, *, before: str | None = None, limit: int = 100
) -> dict[str, Any]:
    """One page, newest first. Pass `next_before` back as `before` for the next (older) page."""
    limit = max(1, min(limit, MAX_PAGE))
    q = _query(session, f)
    if before:
        q = q.where(Event.id < before)
    events = list(session.scalars(q.order_by(Event.id.desc()).limit(limit + 1)))
    more = len(events) > limit
    events = events[:limit]
    return {
        "entries": _rows(session, events),
        "next_before": events[-1].id if more else None,
        "has_more": more,
    }


def facets(session: Session) -> dict[str, Any]:
    """What the filter menus offer: event types seen and the accounts that acted."""
    types = sorted(set(session.scalars(select(Event.type).distinct())))
    actor_ids = set(session.scalars(select(Event.actor_id).distinct()))
    actors = [
        {"id": a.id, "handle": a.handle, "display_name": a.display_name, "kind": a.kind}
        for a in session.scalars(
            select(Account).where(Account.id.in_(actor_ids)).order_by(Account.handle)
        )
    ]
    sections = [
        {"id": s.id, "name": s.name}
        for s in session.scalars(select(Section).order_by(Section.position, Section.name))
    ]
    return {"types": types, "actors": actors, "sections": sections}


CSV_COLUMNS = (
    "id",
    "at",
    "actor",
    "actor_kind",
    "source",
    "type",
    "field",
    "item_id",
    "item_title",
    "list",
    "subject_id",
    "before",
    "after",
    "session_ref",
)


def export_csv(session: Session, f: AuditFilter) -> str:
    """The filtered log as CSV, newest first, up to `EXPORT_MAX` rows."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    before: str | None = None
    sent = 0
    while sent < EXPORT_MAX:
        chunk = page(session, f, before=before, limit=MAX_PAGE)
        for r in chunk["entries"]:
            item = r["item"] or {}
            writer.writerow(
                [
                    r["id"],
                    jsonable(r["at"]),
                    r["actor"]["handle"] or r["actor"]["id"],
                    jsonable(r["actor"]["kind"]) or "",
                    r["source"],
                    r["type"],
                    r["field"] or "",
                    r["item_id"] or "",
                    item.get("title", ""),
                    item.get("list_name") or "",
                    r["subject_id"] or "",
                    "" if r["before"] is None else json.dumps(r["before"], ensure_ascii=False),
                    "" if r["after"] is None else json.dumps(r["after"], ensure_ascii=False),
                    r["session_ref"] or "",
                ]
            )
            sent += 1
        if not chunk["has_more"]:
            break
        before = chunk["next_before"]
    return buf.getvalue()
