"""Today and plate predicates: what is on a person's plate today.

This module owns the three predicates that decide whether an open card belongs on a person's
plate right now, shared by Today, the daily digest, due alerts, agent work eligibility, project
next-step selection, and item views:

- **later**: open, and either `start_at` is in the future, or it has no `start_at` and `due_at`
  is more than the viewer's `lead_days` away (account pref; default 14; 0 turns the rule off).
- **parked**: `parked_until` is set (waiting on an outside party until that workspace date).
- **stale**: open, not later, not parked, and `due_at` is more than 14 days overdue.

Cards in a reference list (`list.reference`) are excluded from all work views.

`today()` returns four groups of open, non-later, non-parked, non-reference items:

- `overdue`: due before now.
- `today`: due between now and the end of today in the workspace timezone.
- `waiting`: `waiting_on_account_id` is the caller (any due date, or none).
- `asked`: open items with an open Ask (assigned comment) addressed to the caller, oldest first.

`sweep()` returns the caller's stale cards (assigned to them), oldest `due_at` first.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.dates import local_day_bounds
from slate.models import (
    Account,
    Comment,
    Item,
    ItemAssignee,
    List,
    Section,
    Status,
    StatusKind,
    utcnow,
)
from slate.service.items import priority_sort_key

DEFAULT_LEAD_DAYS = 14
DEFAULT_SWEEP_WEEKDAY = 4  # Friday (Monday = 0)
STALE_DAYS = 14


def lead_days_for(account: Account | None) -> int:
    """The viewer's `lead_days` pref (default 14; 0 turns the due-date lead rule off)."""
    if account is None:
        return DEFAULT_LEAD_DAYS
    val = (account.prefs_json or {}).get("lead_days")
    if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
        return val
    return DEFAULT_LEAD_DAYS


def sweep_weekday_for(account: Account | None) -> int:
    """The viewer's weekly sweep weekday (0 = Monday .. 6 = Sunday; default 4, Friday)."""
    if account is None:
        return DEFAULT_SWEEP_WEEKDAY
    val = (account.prefs_json or {}).get("sweep_weekday")
    if isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= 6:
        return val
    return DEFAULT_SWEEP_WEEKDAY


def non_reference_list_filter() -> list[Any]:
    """SQL predicate excluding reference lists when `List.reference` exists on the model."""
    col = getattr(List, "reference", None)
    return [col.is_(False)] if col is not None else []


def is_parked(item: Any) -> bool:
    """Whether the card is parked waiting on someone outside."""
    return getattr(item, "parked_until", None) is not None


def is_later(
    item: Any, *, now: datetime, lead_days: int = DEFAULT_LEAD_DAYS, is_open: bool = True
) -> bool:
    """Whether the card is far enough out to stay in Later."""
    if not is_open:
        return False
    start_at = getattr(item, "start_at", None)
    if start_at is not None:
        return start_at > now
    due_at = getattr(item, "due_at", None)
    if lead_days > 0 and due_at is not None:
        return due_at > now + timedelta(days=lead_days)
    return False


def is_stale(
    item: Any, *, now: datetime, lead_days: int = DEFAULT_LEAD_DAYS, is_open: bool = True
) -> bool:
    """Whether the card is open, not later, not parked, and more than 14 days overdue."""
    if not is_open or is_parked(item) or is_later(item, now=now, lead_days=lead_days):
        return False
    due_at = getattr(item, "due_at", None)
    return due_at is not None and (now - due_at) > timedelta(days=STALE_DAYS)


def today(
    session: Session, account_id: str, *, now: datetime | None = None, keys_only: bool = False
) -> dict[str, Any]:
    """`keys_only`: each group holds `(id, priority, due_at, start_at)` rows instead of items, so
    the caller can take the views from the item cache."""
    now = now or utcnow()
    _, end = local_day_bounds(now)
    lead_days = lead_days_for(session.get(Account, account_id))
    cols = (Item.id, Item.priority, Item.due_at, Item.start_at) if keys_only else (Item,)
    open_where = (
        Item.archived_at.is_(None),
        Item.parked_until.is_(None),
        List.archived_at.is_(None),
        Section.archived_at.is_(None),
        *non_reference_list_filter(),
        or_(Status.kind.is_(None), Status.kind.not_in([StatusKind.done, StatusKind.cancelled])),
    )
    rows = session.execute(
        select(*cols)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(*open_where, or_(Item.due_at < end, Item.waiting_on_account_id == account_id))
    )
    groups: dict[str, list[Any]] = {"overdue": [], "today": [], "waiting": []}
    for item in rows if keys_only else (r[0] for r in rows):
        if is_later(item, now=now, lead_days=lead_days):
            continue
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
        .where(*open_where)
        .order_by(first_ask.c.asked_at, Item.id)
    )
    groups["asked"] = [
        item
        for item in (asked if keys_only else (r[0] for r in asked))
        if not is_later(item, now=now, lead_days=lead_days)
    ]
    return {"now": now, "day_ends_at": end, **groups}


def sweep(session: Session, account_id: str, *, now: datetime | None = None) -> list[Item]:
    """The caller's stale cards (open, assigned, not later, not parked, >14 days overdue),
    oldest `due_at` first."""
    now = now or utcnow()
    lead_days = lead_days_for(session.get(Account, account_id))
    cutoff = now - timedelta(days=STALE_DAYS)
    rows = session.scalars(
        select(Item)
        .join(ItemAssignee, ItemAssignee.item_id == Item.id)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(
            ItemAssignee.account_id == account_id,
            Item.archived_at.is_(None),
            Item.parked_until.is_(None),
            Item.due_at.is_not(None),
            Item.due_at < cutoff,
            List.archived_at.is_(None),
            Section.archived_at.is_(None),
            *non_reference_list_filter(),
            or_(Status.kind.is_(None), Status.kind.not_in([StatusKind.done, StatusKind.cancelled])),
        )
        .order_by(Item.due_at.asc(), Item.id.asc())
    ).all()
    return [item for item in rows if is_stale(item, now=now, lead_days=lead_days)]
