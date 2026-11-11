"""/items/{id}/comments, /comments/{id} and /comments/{id}/reactions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import CommentIn, CommentOut, CommentPatch, ReactionIn
from slate.service import comments, item_reads
from slate.service.items import comments_of, get_item

router = APIRouter(tags=["comments"])


def _out(session: DbSession, comment: Any) -> dict:
    return comments.comment_views(session, [comment])[0]


@router.get("/items/{item_id}/comments", response_model=list[CommentOut])
def list_comments(item_id: str, _: ReadAuth, session: DbSession) -> list[dict]:
    """The item's comments, oldest first, with reactions and mentions."""
    return comments.comment_views(session, comments_of(session, get_item(session, item_id).id))


@router.post(
    "/items/{item_id}/comments", status_code=status.HTTP_201_CREATED, response_model=CommentOut
)
def add_comment(item_id: str, body: CommentIn, auth: WriteAuth, session: DbSession) -> dict:
    """`@handle` in the body mentions that account (see webhooks: `assignee_me` hears it).
    `assignee` makes it an Ask: addressed to that account, open until explicitly resolved.
    Replies notify earlier participants but never resolve Asks; `keep_open` is accepted for
    compatibility. A last line `SLATE: done`, `SLATE: review`, `SLATE: later` or `SLATE:
    blocked waiting_on <handle>` also reports back on the task (from an agent: its report,
    later clearing its badge until the due time; from a person, done completes it and blocked
    sets who it waits on)."""
    comment = comments.add_comment(
        session,
        auth.actor,
        item_id,
        body_md=body.body_md,
        assignee=body.assignee,
        keep_open=body.keep_open,
        closing_line=True,
    )
    return _out(session, comment)


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def edit_comment(comment_id: str, body: CommentPatch, auth: WriteAuth, session: DbSession) -> dict:
    """Edit your own comment's text (403 for anyone else's), and/or address it to someone
    (`assignee`, null to take it off; the author or an admin)."""
    sent = body.model_dump(exclude_unset=True)
    comment = comments.get_comment(session, comment_id)
    if sent.get("body_md") is not None:
        comment = comments.edit_comment(session, auth.actor, comment_id, body_md=body.body_md)
    if "assignee" in sent:
        comment = comments.assign_comment(session, auth.actor, comment_id, body.assignee)
    return _out(session, comment)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(comment_id: str, auth: WriteAuth, session: DbSession) -> Response:
    """Delete a comment for good: your own, or anyone's if you are an owner or admin (403
    otherwise). Its reactions go too; the `comment.deleted` event keeps what it said."""
    comments.delete_comment(session, auth.actor, comment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/comments/{comment_id}/resolve", response_model=CommentOut)
def resolve(comment_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Close an Ask: for its assignee, its author or an admin. Idempotent."""
    return _out(session, comments.resolve_comment(session, auth.actor, comment_id))


@router.post("/comments/{comment_id}/reactions", response_model=CommentOut)
def react(comment_id: str, body: ReactionIn, auth: WriteAuth, session: DbSession) -> dict:
    """Add your emoji reaction (idempotent)."""
    return _out(session, comments.react(session, auth.actor, comment_id, emoji=body.emoji))


@router.delete("/comments/{comment_id}/reactions/{emoji}", response_model=CommentOut)
def unreact(comment_id: str, emoji: str, auth: WriteAuth, session: DbSession) -> dict:
    """Take back your emoji reaction (idempotent)."""
    return _out(session, comments.unreact(session, auth.actor, comment_id, emoji=emoji))


class ItemReadOut(BaseModel):
    item_id: str
    read_at: datetime | None


@router.post("/items/{item_id}/read", response_model=ItemReadOut)
def mark_item_read(item_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """You opened the item: its comments so far are no longer new to you, so its card's
    comment dot clears. `read_at` is null when nothing was new (nothing is written)."""
    return {"item_id": item_id, "read_at": item_reads.mark_read(session, auth.actor, item_id)}


@router.get("/me/comment-dots", response_model=dict[str, Literal["agent", "human", "mention"]])
def comment_dots(auth: ReadAuth, session: DbSession) -> dict[str, str]:
    """Item id -> the comment dot its card shows you, for every item with comments new to you
    since you last opened it: `mention` (you are @mentioned or asked, by anyone), `human`
    (a person commented) or `agent` (only agents did). Your own comments never count."""
    return item_reads.comment_dots(session, auth.account.id)
