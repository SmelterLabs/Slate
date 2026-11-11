"""An account's check-in: what it should work on, and what happened that concerns it.

Built for agents polling on a cursor (`GET /checkin`, MCP `slate_checkin`). The people-facing
notifications with read state are `service/inbox.py`. Reads only. Two parts:

- `queue`: open items assigned to the account or waiting on it (not done or cancelled, not
  archived, not in an archived list or section), most urgent first.
- `news`: other accounts' events that concern it, each with `why`:
  `assigned` / `unassigned` (it was added to or removed from the assignees), `mentioned`
  (an @handle in a comment or chat message), `reply` (a reply in a chat thread it wrote in),
  `waiting_on_you`, `asked` (a comment was addressed to it: an Ask), `on_your_item` (any
  change to an item assigned to it), `watching` (any change to an item it watches). Its own
  events are left out.

`since` is an event id: news after it, oldest first, and `next_since` to pass next time. The
cursor advances over events that did not match too, so an agent that keeps `next_since` never
reads an event twice and never misses one. Without `since`, the newest `limit` matches (scanning
at most `TAIL_SCAN` events back) and `next_since` is the newest event id: a cold start.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from slate.models import (
    Account,
    Event,
    Item,
    ItemAssignee,
    List,
    Section,
    Status,
    StatusKind,
    Watcher,
)
from slate.service.core import event_payload
from slate.service.items import PRIORITY_RANK

PAGE = 500
MAX_SCAN = 10_000  # events read per call going forward; past it, has_more and resume
TAIL_SCAN = 5_000
MENTION_TYPES = (
    "item.commented",
    "item.mentioned",
    "comment.edited",
    "message.posted",
    "message.edited",
    "doc.mentioned",
)


def queue(session: Session, account_id: str, limit: int | None = None) -> tuple[list[str], int]:
    """The ids of the `limit` most urgent open items (all when None) and how many there are in
    all. Ids only: the views come from the item cache.

    Starts from the account's assignments (and `waiting_on`), never from the whole items table,
    and ranks and cuts in SQL: an agent holding hundreds of items pays for the few it reads,
    and SQLite hands Python only those rows."""
    mine = (
        select(ItemAssignee.item_id.label("id"))
        .where(ItemAssignee.account_id == account_id)
        .union(select(Item.id).where(Item.waiting_on_account_id == account_id))
        .subquery()
    )
    open_items = (
        select(Item.id)
        .join(mine, mine.c.id == Item.id)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(
            Item.archived_at.is_(None),
            List.archived_at.is_(None),
            Section.archived_at.is_(None),
            or_(Status.kind.is_(None), Status.kind.not_in([StatusKind.done, StatusKind.cancelled])),
        )
    )
    total = session.scalar(select(func.count()).select_from(open_items.subquery())) or 0
    if not total:
        return [], 0
    # items.priority_sort_key in SQL: urgent first, then soonest due, no due date last.
    rank = case(
        *((Item.priority == p, r) for p, r in PRIORITY_RANK.items()), else_=len(PRIORITY_RANK)
    )
    ranked = open_items.order_by(rank, Item.due_at.is_(None), Item.due_at, Item.created_at, Item.id)
    if limit is not None:
        ranked = ranked.limit(limit)
    return list(session.scalars(ranked)), total


def why(ev: Event, account: Account, assigned: set[str], watched: set[str]) -> str | None:
    """Why this event concerns `account`, or None. The account's own events never do (except a
    reminder it set for itself)."""
    if ev.type == "item.reminder":
        return "reminder" if (ev.after_json or {}).get("account_id") == account.id else None
    if ev.type == "account.digest":
        return "digest" if ev.subject_id == account.id else None
    if ev.actor_id == account.id:
        return None
    if ev.type == "item.assigned":
        after, before = ev.after_json or [], ev.before_json or []
        if account.handle in after and account.handle not in before:
            return "assigned"
        if account.handle in before and account.handle not in after:
            return "unassigned"
    after = ev.after_json if isinstance(ev.after_json, dict) else {}
    if ev.type in ("item.commented", "doc.mentioned") and account.id in (
        after.get("reply_to") or []
    ):
        return "reply"
    if ev.type in MENTION_TYPES and account.handle in (after.get("mentions") or []):
        return "mentioned"
    if ev.type == "message.posted":
        return "reply" if account.id in (after.get("thread_followers") or []) else None
    if ev.field == "waiting_on_account_id" and ev.after_json == account.id:
        return "waiting_on_you"
    if ev.type == "comment.assigned" and after.get("assignee_id") == account.id:
        return "asked"
    if ev.type == "item.created":
        return None  # its own item.assigned follows when it matters
    if ev.type == "item.watched" and ev.field == "auto":
        return None  # someone auto-watching (service/watchers.py) is bookkeeping, not news
    if ev.item_id in assigned:
        return "on_your_item"
    if ev.item_id in watched:
        return "watching"
    return None


def _held(session: Session, account_id: str, item_ids: set[str]) -> tuple[set[str], set[str]]:
    """Which of `item_ids` the account is assigned to, and which it watches (two small reads,
    never the account's whole list)."""
    if not item_ids:
        return set(), set()
    assigned = session.scalars(
        select(ItemAssignee.item_id).where(
            ItemAssignee.account_id == account_id, ItemAssignee.item_id.in_(item_ids)
        )
    )
    watched = session.scalars(
        select(Watcher.item_id).where(
            Watcher.account_id == account_id, Watcher.item_id.in_(item_ids)
        )
    )
    return set(assigned), set(watched)


def checkin(
    session: Session,
    account: Account,
    *,
    since: str | None = None,
    limit: int = 50,
    queue_limit: int | None = None,
) -> dict[str, Any]:
    # SQL keeps only the events `why` could say yes to, so a busy board of other people's work
    # is never read into Python; `why` then decides. The cursor still covers every event.
    mine = (
        select(ItemAssignee.item_id)
        .where(ItemAssignee.account_id == account.id)
        .union(select(Watcher.item_id).where(Watcher.account_id == account.id))
    )
    from slate.service.comments import not_about_deleted_comment  # noqa: PLC0415

    base = select(Event).where(
        Event.actor_id != account.id,
        not_about_deleted_comment(),
        or_(
            Event.type.in_(
                ("item.assigned", "item.reminder", "account.digest", "comment.assigned")
            ),
            Event.type.in_(MENTION_TYPES),
            Event.field == "waiting_on_account_id",
            Event.item_id.in_(mine),
        ),
    )
    top = session.scalar(select(func.max(Event.id)))
    news: list[tuple[Event, str]] = []
    has_more = False

    def judged(page: Sequence[Event]) -> Iterator[tuple[Event, str | None]]:
        assigned, watched = _held(session, account.id, {ev.item_id for ev in page if ev.item_id})
        for ev in page:
            yield ev, why(ev, account, assigned, watched)

    if since is None:
        next_since, scanned, before = top, 0, None
        while scanned < TAIL_SCAN and len(news) < limit:
            q = base.order_by(Event.id.desc()).limit(min(PAGE, TAIL_SCAN - scanned))
            page = session.scalars(q if before is None else q.where(Event.id < before)).all()
            for ev, reason in judged(page):
                if reason:
                    news.append((ev, reason))
                    if len(news) == limit:
                        break
            scanned += len(page)
            if len(page) < PAGE:
                break
            before = page[-1].id
        news.reverse()
    else:
        next_since, scanned, stopped = since, 0, False
        while scanned < MAX_SCAN and top is not None:
            page = session.scalars(
                base.where(Event.id > next_since, Event.id <= top).order_by(Event.id).limit(PAGE)
            ).all()
            for ev, reason in judged(page):
                scanned += 1
                next_since = ev.id
                if reason:
                    news.append((ev, reason))
                    if len(news) == limit:
                        break
            if len(news) == limit or len(page) < PAGE:
                stopped = len(news) == limit
                break
        else:
            stopped = top is not None
        if stopped:
            # Call again now if more candidates wait before the snapshot.
            has_more = (
                session.scalar(
                    base.with_only_columns(Event.id)
                    .where(Event.id > next_since, Event.id <= top)
                    .limit(1)
                )
                is not None
            )
        if not has_more and top is not None and top > next_since:
            next_since = top  # nothing else up to the snapshot concerns this account

    titles = dict(
        session.execute(
            select(Item.id, Item.title).where(
                Item.id.in_({e.item_id for e, _ in news if e.item_id})
            )
        ).all()
    )
    rows, total = queue(session, account.id, queue_limit)
    return {
        "queue": rows,
        "queue_total": total,
        "news": [
            {**event_payload(ev), "why": reason, "item_title": titles.get(ev.item_id)}
            for ev, reason in news
        ],
        "next_since": next_since,
        "has_more": has_more,
    }
