"""Revert from the log: undo one recorded item change by issuing its inverse as the caller.

    revert_event(session, actor, event_id) -> Item     # POST /events/{id}/revert, slate_item revert

The log is append-only (rule 3), so nothing is edited: the inverse goes through the same
service functions the original change used (`items.update_item`, `items.bulk_update` for a
move), as the caller, with the usual access checks, and writes its own event whose
`session_ref` is `revert:<event id>`. That event is the record; there is no `item.reverted`.

What can be reverted: `item.updated` on a plain field (title, priority, dates, waiting on,
acceptance, estimate, kind, external ref, position, the description, the tags),
`item.status_changed`, `item.assigned` and `item.moved`. The field's current value must still
be the event's `after`: if someone changed it again since, `Conflict` ("changed since") says so
and nothing is written. Anything else (a creation, a comment, a report...) is `Invalid`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Account, Event, Item, ItemAssignee, ItemTag, Tag
from slate.service import items as items_svc
from slate.service.core import Actor, jsonable
from slate.service.errors import Conflict, Invalid, NotFound

# `item.updated` fields that are a plain `update_item` change (field -> change key).
FIELDS = {
    "title": "title",
    "priority": "priority",
    "start_at": "start_at",
    "due_at": "due_at",
    "due_date_only": "due_date_only",
    "waiting_on_account_id": "waiting_on_account_id",
    "acceptance_md": "acceptance_md",
    "estimate_minutes": "estimate_minutes",
    "kind": "kind",
    "external_ref": "external_ref",
    "position": "position",
    "body_md": "body_md",
    "tags": "tags",
}
TYPES = ("item.updated", "item.status_changed", "item.assigned", "item.moved")
# A due_at change records its due_date_only sibling in the same change, right after it.
SIBLING_WINDOW = timedelta(seconds=5)


def revert_event(session: Session, actor: Actor, event_id: str) -> Item:
    """Issue the inverse of `event_id` (module docstring) and return the item."""
    ev = session.scalar(select(Event).where(Event.id == event_id))
    if ev is None:
        raise NotFound(f"event {event_id} not found")
    if ev.type not in TYPES or (ev.type == "item.updated" and ev.field not in FIELDS):
        what = f"{ev.type} on {ev.field}" if ev.field else ev.type
        raise Invalid(f"{what} cannot be reverted: only field changes, status, assignees and moves")
    if ev.item_id is None:
        raise Invalid(f"{ev.type} is not an item change")
    item = items_svc.get_item(session, ev.item_id)
    current = _current(session, item, ev)
    if jsonable(current) != ev.after_json:
        raise Conflict(
            f"{_name(ev)} has changed since: it is now {_show(current)}, not {_show(ev.after_json)}"
        )
    by = replace(actor, session_ref=f"revert:{ev.id}")
    before = ev.before_json
    if ev.type == "item.moved":
        items_svc.bulk_update(session, by, [item.id], {"list_id": before})
        return items_svc.get_item(session, item.id)
    if ev.type == "item.status_changed":
        if before is None:
            raise Invalid("it had no status before; pick one instead")
        return items_svc.update_item(session, by, item.id, {"status": before})
    if ev.type == "item.assigned":
        return items_svc.update_item(session, by, item.id, {"assignees": list(before or [])})
    key = FIELDS[ev.field]
    changes: dict[str, Any] = {key: before}
    if ev.field == "due_at":
        flag = _sibling_flag(session, ev)
        if flag is not None:
            changes["due_date_only"] = flag
    elif ev.field == "due_date_only":
        changes = {"due_at": jsonable(item.due_at), "due_date_only": bool(before)}
    return items_svc.update_item(session, by, item.id, changes)


def _current(session: Session, item: Item, ev: Event) -> Any:
    if ev.type == "item.moved":
        return item.list_id
    if ev.type == "item.status_changed":
        return item.status_id
    if ev.type == "item.assigned":
        return sorted(
            session.scalars(
                select(Account.handle)
                .join(ItemAssignee, ItemAssignee.account_id == Account.id)
                .where(ItemAssignee.item_id == item.id)
            )
        )
    if ev.field == "tags":
        return sorted(
            session.scalars(
                select(Tag.name)
                .join(ItemTag, ItemTag.tag_id == Tag.id)
                .where(ItemTag.item_id == item.id)
            )
        )
    return getattr(item, ev.field)


def _sibling_flag(session: Session, ev: Event) -> bool | None:
    """The `before` of the `due_date_only` event recorded with a `due_at` change, if any."""
    sibling = session.scalar(
        select(Event)
        .where(
            Event.item_id == ev.item_id,
            Event.actor_id == ev.actor_id,
            Event.type == "item.updated",
            Event.field == "due_date_only",
            Event.id > ev.id,
            Event.at <= ev.at + SIBLING_WINDOW,
        )
        .order_by(Event.id)
        .limit(1)
    )
    return bool(sibling.before_json) if sibling is not None else None


def _name(ev: Event) -> str:
    return {
        "item.moved": "the list",
        "item.status_changed": "the status",
        "item.assigned": "the assignees",
    }.get(ev.type, ev.field or ev.type)


def _show(value: Any) -> str:
    if value is None or value == [] or value == "":
        return "empty"
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."
