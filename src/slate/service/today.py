"""Today: the caller's day across every section.

Three groups, each item in exactly one (first match wins), open items only (not done or
cancelled, not archived, not in an archived list or section):

- `overdue`: due before now.
- `today`: due between now and the end of today in the workspace timezone.
- `waiting`: `waiting_on_account_id` is the caller (any due date, or none).

And `asked` ("Asked of you"): open items with an open Ask (assigned comment) addressed to the
caller, oldest Ask first. An item can be in `asked` and in one of the three.

Each group is sorted by priority (urgent, high, normal, low, none), then soonest due.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.dates import local_day_bounds
from slate.models import Comment, Item, List, Section, Status, StatusKind, utcnow
from slate.service.items import priority_sort_key


def today(
    session: Session, account_id: str, *, now: datetime | None = None, keys_only: bool = False
) -> dict[str, Any]:
    """`keys_only`: each group holds `(id, priority, due_at)` rows instead of items, so the
    caller can take the views from the item cache (thousands of overdue items on a big board)."""
    now = now or utcnow()
    _, end = local_day_bounds(now)
    cols = (Item.id, Item.priority, Item.due_at) if keys_only else (Item,)
    rows = session.execute(
        select(*cols)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(
            Item.archived_at.is_(None),
            List.archived_at.is_(None),
            Section.archived_at.is_(None),
            or_(Status.kind.is_(None), Status.kind.not_in([StatusKind.done, StatusKind.cancelled])),
            or_(Item.due_at < end, Item.waiting_on_account_id == account_id),
        )
    )
    groups: dict[str, list[Any]] = {"overdue": [], "today": [], "waiting": []}
    for item in rows if keys_only else (r[0] for r in rows):
        if item.due_at is not None and item.due_at < now:
            groups["overdue"].append(item)
        elif item.due_at is not None and item.due_at < end:
            groups["today"].append(item)
        else:
            groups["waiting"].append(item)
    for items in groups.values():
        items.sort(key=priority_sort_key)
    first_ask = (
        select(Comment.item_id, func.min(Comment.created_at).label("asked_at"))
        .where(Comment.assignee_id == account_id, Comment.resolved_at.is_(None))
        .group_by(Comment.item_id)
        .subquery()
    )
    asked = session.execute(
        select(*cols)
        .join(first_ask, first_ask.c.item_id == Item.id)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(
            Item.archived_at.is_(None),
            List.archived_at.is_(None),
            Section.archived_at.is_(None),
            or_(Status.kind.is_(None), Status.kind.not_in([StatusKind.done, StatusKind.cancelled])),
        )
        .order_by(first_ask.c.asked_at, Item.id)
    )
    groups["asked"] = [r for r in asked] if keys_only else [r[0] for r in asked]
    return {"now": now, "day_ends_at": end, **groups}
