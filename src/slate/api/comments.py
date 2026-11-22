"""/items/{id}/comments, /comments/{id} and /comments/{id}/reactions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import CommentIn, CommentOut, CommentPatch, ReactionIn
from slate.service import comments, item_reads, work
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
    """`@handle` in the body mentions that account in their inbox.
    `assignee` makes it an Ask: addressed to that account, open until explicitly resolved.
    Replies notify earlier participants but never resolve Asks; `keep_open` is accepted for
    compatibility. A last line `SLATE: done`, `SLATE: review`, `SLATE: later`, `SLATE:
    answered` or up to four trailing `SLATE: ask <handle>: <question> [option | option]`
    lines for the same person also reports back on the task. An agent's ask stores the
    questions on its comment and uses the preceding text as its reply. An explicit
    `assignee` must be valid and reachable. When the closing Ask applies, its recipient
    is the comment's assignee. A malformed agent ask returns 422 with the validation
    reason and posts nothing. A person's `done`
    completes the task."""
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


class AnswerIn(BaseModel):
    option: str = Field(min_length=1, description="One of the ask's options, exactly")


@router.post("/comments/{comment_id}/answer", response_model=CommentOut)
def answer(comment_id: str, body: AnswerIn, auth: WriteAuth, session: DbSession) -> dict:
    """Answer an agent's question (a comment with `ask`) with one of its options, for the
    person asked or an admin: posts your comment whose text is the option, the card stops
    waiting on the person, the question is resolved (`reason: answered`) and the agent is
    woken with your reply, as a typed reply does. Returns your new comment. 422 for an option
    it does not offer or when the ask has several questions (answer them in one comment),
    409 when it was already answered."""
    return _out(session, comments.answer(session, auth.actor, comment_id, option=body.option))


class RunLine(BaseModel):
    at: datetime
    text: str


class RunLogOut(BaseModel):
    comment_id: str
    account_id: str = Field(description="The agent whose run it was")
    started_at: datetime | None
    finished_at: datetime
    lines: list[RunLine] = Field(description="What the run posted while it worked, oldest first")


@router.get("/comments/{comment_id}/run", response_model=RunLogOut)
def run_log(comment_id: str, _: ReadAuth, session: DbSession) -> dict:
    """The run log kept with an agent's answer: the lines its run posted while it worked
    (`POST /work/{id}/activity`: tool calls, what it was thinking), stored when the run
    finished. A comment with one carries `run_steps`. 404 when the comment has no log (it was
    not a run's answer, the run posted no lines, or the server restarted mid-run)."""
    return work.run_log(session, comment_id)


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
