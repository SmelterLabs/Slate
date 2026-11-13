"""An item's activity feed: its comments and its history in one time-ordered list, the way the
task window's right-hand column shows it (and the way an agent reads "what happened here").

Events that the feed already shows another way are left out: `item.commented` (the comment
itself is an entry), `comment.reacted`/`comment.unreacted` (shown on the comment), and
`comment.edited` (the comment carries `edited_at`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Event
from slate.service.comments import comment_views
from slate.service.core import event_payload
from slate.service.items import comments_of, get_item

FOLDED = frozenset({"item.commented", "comment.reacted", "comment.unreacted", "comment.edited"})


def activity(
    session: Session, item_id: str, *, include: str = "all", limit: int | None = None
) -> list[dict[str, Any]]:
    """Entries `{kind: comment|event, at, comment|event}`, oldest first.

    `include` is `all`, `comments` or `events`. With `limit`, the newest `limit` entries.
    """
    item = get_item(session, item_id)
    entries: list[dict[str, Any]] = []
    if include in ("all", "comments"):
        for c in comment_views(session, comments_of(session, item.id)):
            entries.append({"kind": "comment", "at": c["created_at"], "comment": c})
    if include in ("all", "events"):
        for ev in session.scalars(
            select(Event)
            .where(Event.item_id == item.id, Event.type.not_in(FOLDED))
            .order_by(Event.id)
        ):
            entries.append({"kind": "event", "at": ev.at, "event": event_payload(ev)})
    entries.sort(key=lambda e: e["at"])
    if limit is not None:
        entries = entries[-limit:] if limit > 0 else []
    return entries
