"""Where an event-log engine has read up to, and the walk push and email share.

    engine_cursor(session, name)      the `engine_cursors` row, made at the newest event
    follow(session, name, now=, window=, handle=, follows=, active=)

Automations, push and email each keep one `engine_cursors` row. A new row starts at the newest
event, so a new install never replays history. Bookkeeping, like deliveries: no events.

`follow` is the notification walk: every event after the cursor up to the newest one at the
start, at most `window` old, page by page. For each event `handle(ev)` returns the rows to
queue (or None); they are added, and the cursor moves, in one commit per page, before any
network I/O. Then the cursor jumps to the newest event, so a quiet engine (no recipients, or
`active=False`) never builds a backlog. On the way it keeps `follows` current (a new watcher
hears about what follows in the same batch) and skips repeat description saves (`EDIT_QUIET`).

One event whose handler raises is logged and skipped, so it cannot hold up every event after
it; a database error is the whole step's failure (the engine loop reports and retries it).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from slate.models import EngineCursor, Event, utcnow

log = logging.getLogger(__name__)

PAGE = 500
# Description autosaves: one notification per author and item until they pause this long.
EDIT_QUIET = timedelta(minutes=10)


def engine_cursor(session: Session, name: str) -> EngineCursor:
    """The cursor row `name`, created (and committed) at the newest event if it is missing."""
    row = session.get(EngineCursor, name)
    if row is None:
        row = EngineCursor(
            name=name, event_id=session.scalar(select(func.max(Event.id))), updated_at=utcnow()
        )
        session.add(row)
        session.commit()
    return row


def _repeat_edit(session: Session, ev: Event) -> bool:
    """A description save shortly after the same author's last one on the same item."""
    if ev.type != "item.updated" or ev.field != "body_md":
        return False
    prev = session.scalar(
        select(Event.at)
        .where(
            Event.item_id == ev.item_id,
            Event.type == "item.updated",
            Event.field == "body_md",
            Event.actor_id == ev.actor_id,
            Event.id < ev.id,
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    return prev is not None and ev.at - prev < EDIT_QUIET


def follow(
    session: Session,
    name: str,
    *,
    now: datetime,
    window: timedelta,
    handle: Callable[[Event], Iterable[Any] | None],
    follows: dict[str, Any] | None = None,
    refresh: Callable[[str], Any] | None = None,
    active: bool = True,
) -> None:
    cursor = engine_cursor(session, name)
    top = session.scalar(select(func.max(Event.id)))
    if active and top:
        while True:
            q = select(Event).where(Event.id <= top, Event.at >= now - window)
            if cursor.event_id:
                q = q.where(Event.id > cursor.event_id)
            page = list(session.scalars(q.order_by(Event.id).limit(PAGE)))
            if not page:
                break
            for ev in page:
                if (
                    follows is not None
                    and refresh is not None
                    and ev.type == "item.watched"
                    and ev.after_json in follows
                ):
                    follows[ev.after_json] = refresh(ev.after_json)
                if _repeat_edit(session, ev):
                    continue
                try:
                    rows = list(handle(ev) or ())
                except SQLAlchemyError:
                    raise
                except Exception:
                    log.exception("%s: skipping event %s, which could not be handled", name, ev.id)
                    continue
                session.add_all(rows)
            # Rows and the cursor are bookkeeping: one commit per page, before any send.
            cursor.event_id, cursor.updated_at = page[-1].id, now
            session.commit()
            if len(page) < PAGE:
                break
    if top and cursor.event_id != top:
        cursor.event_id, cursor.updated_at = top, now
        session.commit()
