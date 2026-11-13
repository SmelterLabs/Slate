"""The trash (ClickUp-style): delete a task without losing it straight away.

Trashing stamps `trashed_at` on the item and its subtasks and, when they are not archived
already, `archived_at` with the same instant, so every path that hides archived items (the
board, Today, check-in, due events, recurrence's open instance, automations, blockers) hides
trashed ones with no change. One `item.trashed` event per item, `after = {trashed_at,
archived_at}`.

Restoring clears `trashed_at`, and `archived_at` only where the trash set it (an item archived
before it was trashed comes back archived). Subtasks trashed with their parent come back with
it; a subtask cannot be restored while its parent is in the trash. One `item.restored` each.

Purging deletes for good (subtasks first): comments, checklists, files and links go with the
row by cascade; its events stay, plus `item.purged` holding its last state. Only trashed
items can be purged. The scheduler purges items trashed more than `settings.trash_days` ago
(`purge_expired`, as the `slate` system agent); `empty_trash` purges everything (admins).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Item, utcnow
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Invalid, NotFound
from slate.settings import settings

PURGE_BATCH = 200


def _get(session: Session, item_id: str) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise NotFound(f"item {item_id} not found")
    return item


def _children(session: Session, item: Item) -> list[Item]:
    """Every subtask under `item`, at any depth (subtasks nest four deep), parents first."""
    out: list[Item] = []
    frontier = [item.id]
    while frontier:
        rows = list(session.scalars(select(Item).where(Item.parent_id.in_(frontier))))
        out.extend(rows)
        frontier = [r.id for r in rows]
    return out


# --- Reads ---------------------------------------------------------------------------------


def purge_due_at(item: Item) -> datetime | None:
    """When the scheduler will delete this trashed item for good (None: never, or not trashed)."""
    if item.trashed_at is None or settings.trash_days <= 0:
        return None
    return item.trashed_at + timedelta(days=settings.trash_days)


# --- Writes --------------------------------------------------------------------------------


def _trash_one(cx: Change, item: Item, now: datetime) -> None:
    if item.trashed_at is not None:
        return
    before = {"trashed_at": None, "archived_at": item.archived_at}
    item.trashed_at = now
    if item.archived_at is None:
        item.archived_at = now
    cx.record(
        "item.trashed",
        item_id=item.id,
        field="trashed_at",
        before=before,
        after={"trashed_at": now, "archived_at": item.archived_at},
        covers=[item],
    )


def trash_item(
    session: Session, actor: Actor, item_id: str, *, now: datetime | None = None
) -> Item:
    """Move the item and its subtasks to the trash. Trashing twice is a no-op."""
    item = _get(session, item_id)
    now = now or utcnow()
    targets = [item, *_children(session, item)]
    with apply_change(session, actor) as cx:
        for target in targets:
            _trash_one(cx, target, now)
    return item


def trash_items(
    session: Session, actor: Actor, items: list[Item], *, now: datetime | None = None
) -> list[Item]:
    """Trash many items (and their subtasks) in one change, all at the same instant."""
    now = now or utcnow()
    with apply_change(session, actor) as cx:
        for item in items:
            for target in [item, *_children(session, item)]:
                _trash_one(cx, target, now)
    return items


def restore_item(session: Session, actor: Actor, item_id: str) -> Item:
    """Take the item (and the subtasks trashed with it) out of the trash."""
    return restore_items(session, actor, [_get(session, item_id)])[0]


def restore_items(session: Session, actor: Actor, items: list[Item]) -> list[Item]:
    """Restore many items in one change. Refused (nothing restored) when one's parent is still
    trashed and not being restored with it."""
    picked = {i.id for i in items}
    for item in items:
        if item.trashed_at is None or item.parent_id is None or item.parent_id in picked:
            continue
        parent = session.get(Item, item.parent_id)
        if parent is not None and parent.trashed_at is not None:
            raise Invalid("its parent task is in the trash; restore the parent")
    targets: list[Item] = []
    for item in items:
        if item.trashed_at is None:
            continue
        when = item.trashed_at
        targets += [item, *(c for c in _children(session, item) if c.trashed_at == when)]
    seen: set[str] = set()
    with apply_change(session, actor) as cx:
        for target in targets:
            if target.id in seen or target.trashed_at is None:
                continue
            seen.add(target.id)
            before = {"trashed_at": target.trashed_at, "archived_at": target.archived_at}
            if target.archived_at == target.trashed_at:
                target.archived_at = None
            target.trashed_at = None
            cx.record(
                "item.restored",
                item_id=target.id,
                field="trashed_at",
                before=before,
                after={"trashed_at": None, "archived_at": target.archived_at},
                covers=[target],
            )
    return items


def _purge(cx: Change, item: Item) -> None:
    for child in _children(cx.session, item):
        cx.delete(child, "item.purged")
    cx.session.flush()  # children go before the parent row they point at
    cx.delete(item, "item.purged")


def purge_item(session: Session, actor: Actor, item_id: str) -> None:
    """Delete a trashed item for good (with its subtasks). Its events stay."""
    item = _get(session, item_id)
    if item.trashed_at is None:
        raise Invalid("only items in the trash can be deleted for good; trash it first")
    if item.parent_id is not None:
        parent = session.get(Item, item.parent_id)
        if parent is not None and parent.trashed_at is not None:
            raise Invalid("its parent task is in the trash; delete the parent")
    with apply_change(session, actor) as cx:
        _purge(cx, item)


def _top_trashed(session: Session, *, before: datetime | None = None) -> list[Item]:
    """Trashed items whose parent is not also trashed (purging those takes the subtasks)."""
    stmt = select(Item).where(Item.trashed_at.is_not(None))
    if before is not None:
        stmt = stmt.where(Item.trashed_at < before)
    rows = list(session.scalars(stmt.order_by(Item.trashed_at).limit(PURGE_BATCH * 5)))
    ids = {i.id for i in rows}
    return [i for i in rows if i.parent_id not in ids][:PURGE_BATCH]


def empty_trash(session: Session, actor: Actor) -> int:
    """Delete everything in the trash for good. Returns how many items went."""
    count = 0
    while batch := _top_trashed(session):
        with apply_change(session, actor) as cx:
            for item in batch:
                count += 1 + len(_children(session, item))
                _purge(cx, item)
    return count


def purge_expired(session: Session, now: datetime) -> int:
    """Scheduler hook: delete items trashed more than `settings.trash_days` ago."""
    if settings.trash_days <= 0:
        return 0
    from slate.service.due import system_actor

    cutoff = now - timedelta(days=settings.trash_days)
    batch = _top_trashed(session, before=cutoff)
    if not batch:
        return 0
    actor = system_actor(session)
    count = 0
    with apply_change(session, actor) as cx:
        for item in batch:
            count += 1 + len(_children(session, item))
            _purge(cx, item)
    return count
