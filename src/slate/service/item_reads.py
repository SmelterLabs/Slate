"""Comment dots: which items hold comments that are new to an account since it last opened them.

The board's card shows one dot in its bottom-right corner (docs/board.md "Comment dots"):

- `mention`: a comment that @mentions the account, or an Ask addressed to it, by anyone
  (person or agent). The board pulses it.
- `human`: a comment by a person (a member or a guest).
- `agent`: only agents have commented.

The strongest one wins. "New" means written after the account's `ItemRead.read_at` for the
item, or after the account was created when it has never opened it. The account's own comments
never count, and a guest is never told about a workspace agent's comment (service/guests.py).

Opening the task window calls `mark_read` (`POST /items/{id}/read`), recorded as one
`item.read` event whose subject is the account (no `item_id`, so it stays out of the item's
history and webhooks about the item). It writes nothing when there is nothing to clear, so
opening a card many times a day costs no rows. Reading the comment in the Inbox does not clear
the dot: only opening the card does.

The dots are per account, so they are not part of the item view (service/item_cache.py serves
one view to every caller): the board reads them from `GET /me/comment-dots` and reloads them
when a comment, mention, Ask or its own `item.read` arrives.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, Comment, Item, ItemRead, Role, utcnow
from slate.service import guests
from slate.service.comments import MENTION_RE
from slate.service.core import Actor, apply_change
from slate.service.items import get_item

Dot = Literal["agent", "human", "mention"]
RANK: dict[str, int] = {"agent": 0, "human": 1, "mention": 2}


def _new_comments(session: Session, account: Account, item_ids: list[str] | None = None):  # noqa: ANN202
    """(item_id, assignee_id, body_md, author kind) of each comment new to `account`."""
    q = (
        select(Comment.item_id, Comment.assignee_id, Comment.body_md, Account.kind)
        .join(Account, Account.id == Comment.author_id)
        .join(Item, Item.id == Comment.item_id)
        .outerjoin(
            ItemRead,
            and_(ItemRead.item_id == Comment.item_id, ItemRead.account_id == account.id),
        )
        .where(
            Comment.author_id != account.id,
            or_(
                Comment.created_at > ItemRead.read_at,
                and_(ItemRead.read_at.is_(None), Comment.created_at > account.created_at),
            ),
            Item.trashed_at.is_(None),
        )
    )
    if item_ids is not None:
        q = q.where(Comment.item_id.in_(item_ids))
    if guests.viewer_is_guest(session) or account.role == Role.guest:
        hidden = guests.workspace_agent_ids(session)
        if hidden:
            q = q.where(Comment.author_id.not_in(hidden))
    return session.execute(q)


def _dot(account: Account, assignee_id: str | None, body_md: str, kind: AccountKind) -> Dot:
    if assignee_id == account.id:
        return "mention"
    if account.handle in {m.lower() for m in MENTION_RE.findall(body_md or "")}:
        return "mention"
    return "human" if kind == AccountKind.person else "agent"


def comment_dots(session: Session, account_id: str) -> dict[str, Dot]:
    """Item id -> the strongest dot for `account_id`, for every visible item that has one."""
    account = session.get(Account, account_id)
    if account is None:
        return {}
    out: dict[str, Dot] = {}
    for item_id, assignee_id, body_md, kind in _new_comments(session, account):
        dot = _dot(account, assignee_id, body_md, kind)
        if item_id not in out or RANK[dot] > RANK[out[item_id]]:
            out[item_id] = dot
    return out


def mark_read(session: Session, actor: Actor, item_id: str) -> datetime | None:
    """The caller opened the item: every comment on it so far is no longer new. Returns the
    new `read_at`, or None when nothing was new (then nothing is written)."""
    item = get_item(session, item_id)
    account = session.get(Account, actor.account_id)
    if account is None or _new_comments(session, account, [item.id]).first() is None:
        return None
    now = utcnow()
    with apply_change(session, actor) as cx:
        row = session.get(ItemRead, (account.id, item.id), populate_existing=True)
        if row is None:
            row = ItemRead(account_id=account.id, item_id=item.id, read_at=now)
            session.add(row)
        else:
            row.read_at = now
        cx.record(
            "item.read",
            subject_id=account.id,
            after={"item_id": item.id, "read_at": now.isoformat()},
            covers=[row],
        )
    return now
