"""Checklists inside an item: named lists of small to-dos that are not subtasks (ClickUp's
checklists). Each item can hold several; each checklist holds ordered entries that are ticked
done or not. Every write is an event on the owning item, so the item's history shows them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.models import Checklist, ChecklistItem, utcnow
from slate.service import access
from slate.service.core import Actor, apply_change
from slate.service.errors import Invalid, NotFound
from slate.service.items import get_item, resolve_account


def _name(value: str | None, what: str = "name", limit: int = 200) -> str:
    name = (value or "").strip()
    if not name:
        raise Invalid(f"checklist {what} is empty")
    if len(name) > limit:
        raise Invalid(f"checklist {what} is longer than {limit} characters")
    return name


def get_checklist(session: Session, checklist_id: str) -> Checklist:
    cl = session.get(Checklist, checklist_id)
    if cl is None:
        raise NotFound(f"checklist {checklist_id} not found")
    return cl


def get_entry(session: Session, entry_id: str) -> ChecklistItem:
    entry = session.get(ChecklistItem, entry_id)
    if entry is None:
        raise NotFound(f"checklist entry {entry_id} not found")
    return entry


def checklists_of(session: Session, item_id: str) -> list[dict[str, Any]]:
    """The item's checklists in order, each with its entries in order."""
    lists = list(
        session.scalars(
            select(Checklist)
            .where(Checklist.item_id == item_id)
            .order_by(Checklist.position, Checklist.created_at)
        )
    )
    entries: dict[str, list[ChecklistItem]] = defaultdict(list)
    for e in session.scalars(
        select(ChecklistItem)
        .where(ChecklistItem.item_id == item_id)
        .order_by(ChecklistItem.position, ChecklistItem.created_at)
    ):
        entries[e.checklist_id].append(e)
    # A guest's copy names no workspace agent as an entry's assignee.
    return access.scrub(
        session,
        [
            {
                "id": cl.id,
                "item_id": cl.item_id,
                "name": cl.name,
                "position": cl.position,
                "created_at": cl.created_at,
                "items": [
                    {
                        "id": e.id,
                        "checklist_id": e.checklist_id,
                        "title": e.title,
                        "done": e.done_at is not None,
                        "done_at": e.done_at,
                        "assignee_id": e.assignee_id,
                        "position": e.position,
                    }
                    for e in entries[cl.id]
                ],
            }
            for cl in lists
        ],
    )


def _next_position(session: Session, model: Any, column: Any, value: str) -> int:
    top = session.scalar(select(func.max(model.position)).where(column == value))
    return 0 if top is None else top + 1


def create_checklist(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    name: str = "Checklist",
    items: Sequence[str] = (),
) -> Checklist:
    """A checklist on the item, optionally with its first entries (one event each)."""
    item = get_item(session, item_id)
    name = _name(name)
    titles = [_name(t, "entry", 500) for t in items]
    with apply_change(session, actor) as cx:
        cl = cx.create(
            Checklist(
                item_id=item.id,
                name=name,
                position=_next_position(session, Checklist, Checklist.item_id, item.id),
            ),
            "checklist.created",
        )
        for pos, title in enumerate(titles):
            cx.create(
                ChecklistItem(checklist_id=cl.id, item_id=item.id, title=title, position=pos),
                "checklist.entry_added",
            )
    return cl


def update_checklist(
    session: Session, actor: Actor, checklist_id: str, changes: Mapping[str, Any]
) -> Checklist:
    """Rename (`name`) or reorder (`position`)."""
    cl = get_checklist(session, checklist_id)
    unknown = set(changes) - {"name", "position"}
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    with apply_change(session, actor) as cx:
        if "name" in changes:
            cx.set(cl, "name", _name(changes["name"]), "checklist.updated")
        if changes.get("position") is not None:
            cx.set(cl, "position", int(changes["position"]), "checklist.updated")
    return cl


def delete_checklist(session: Session, actor: Actor, checklist_id: str) -> None:
    """Remove a checklist and its entries. The `checklist.deleted` event keeps what it held."""
    cl = get_checklist(session, checklist_id)
    entries = list(
        session.scalars(select(ChecklistItem).where(ChecklistItem.checklist_id == cl.id))
    )
    with apply_change(session, actor) as cx:
        cx.record(
            "checklist.deleted",
            item_id=cl.item_id,
            subject_id=cl.id,
            before={"name": cl.name, "items": [e.title for e in entries]},
            covers=[cl],
        )
        session.delete(cl)  # its entries go by ON DELETE CASCADE


def add_entry(
    session: Session,
    actor: Actor,
    checklist_id: str,
    *,
    title: str,
    assignee: str | None = None,
    position: int | None = None,
) -> ChecklistItem:
    cl = get_checklist(session, checklist_id)
    title = _name(title, "entry", 500)
    assignee_id = resolve_account(session, assignee).id if assignee else None
    if position is None:
        position = _next_position(session, ChecklistItem, ChecklistItem.checklist_id, cl.id)
    with apply_change(session, actor) as cx:
        return cx.create(
            ChecklistItem(
                checklist_id=cl.id,
                item_id=cl.item_id,
                title=title,
                assignee_id=assignee_id,
                position=int(position),
            ),
            "checklist.entry_added",
        )


def update_entry(
    session: Session, actor: Actor, entry_id: str, changes: Mapping[str, Any]
) -> ChecklistItem:
    """Change `title`, `done` (true/false), `assignee` (handle/id or null) or `position`.

    Ticking records `checklist.entry_checked` / `checklist.entry_unchecked` so the history reads
    as what happened rather than a timestamp diff.
    """
    entry = get_entry(session, entry_id)
    unknown = set(changes) - {"title", "done", "assignee", "position"}
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    values: dict[str, Any] = {}
    if "title" in changes:
        values["title"] = _name(changes["title"], "entry", 500)
    if "assignee" in changes:
        ref = changes["assignee"]
        values["assignee_id"] = resolve_account(session, ref).id if ref else None
    if changes.get("position") is not None:
        values["position"] = int(changes["position"])
    done = changes.get("done")
    if "done" in changes and not isinstance(done, bool):
        raise Invalid("done must be true or false")
    with apply_change(session, actor) as cx:
        for key, value in values.items():
            cx.set(entry, key, value, "checklist.entry_updated")
        if done is not None and done != (entry.done_at is not None):
            entry.done_at = utcnow() if done else None
            cx.record(
                "checklist.entry_checked" if done else "checklist.entry_unchecked",
                item_id=entry.item_id,
                subject_id=entry.id,
                field="done",
                before=not done,
                after={"title": entry.title, "done": done},
                covers=[entry],
            )
    return entry


def delete_entry(session: Session, actor: Actor, entry_id: str) -> None:
    entry = get_entry(session, entry_id)
    with apply_change(session, actor) as cx:
        cx.delete(entry, "checklist.entry_removed")
