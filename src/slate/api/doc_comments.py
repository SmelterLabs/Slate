"""/docs/{id}/comments, /docs/{id}/activity and /doc-comments/{id}: discussion and review
threads on pages and files (service/doc_threads.py).

Select text on a page (or a page and a spot on a file) and comment with an `anchor`: that is a
review thread, with replies, Resolve, and on a page an optional `suggestion_md` the reader can
Accept. A comment without an anchor is discussion. Every comment is stamped with the revision
it was written on (`anchor_revision`, `outdated` when the doc has moved on). `@handle`
mentions and `assignee` (an Ask) reach the inbox, check-in, push and webhooks as
`doc.mentioned`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.docs import DocOut, out
from slate.service import doc_threads

router = APIRouter(tags=["docs"])

Surface = Literal["review", "discussion"]


class AnchorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str | None = Field(default=None, max_length=2000, description="Page text selected")
    start: int | None = Field(default=None, ge=0, description="Offset of the quote in body_md")
    end: int | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1, description="A PDF page")
    x: float | None = Field(default=None, ge=0, le=1, description="A spot: 0-1 across")
    y: float | None = Field(default=None, ge=0, le=1, description="A spot: 0-1 down")


class DocCommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_md: str = Field(min_length=1, max_length=20_000)
    parent_id: str | None = Field(default=None, description="Reply in this thread (one level)")
    viewed_revision: int | None = Field(
        default=None,
        ge=0,
        description="Viewed file revision or page save revision; replies inherit the root",
    )
    anchor: AnchorIn | None = Field(
        default=None, description="Starts a review thread on a span, a page or a spot"
    )
    suggestion_md: str | None = Field(
        default=None,
        max_length=20_000,
        description="Pages only, with a quoted anchor: the text to put in place of the quote",
    )
    assignee: str | None = Field(
        default=None, description="Handle or id: an Ask for that account, open until answered"
    )


class DocCommentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_md: str = Field(min_length=1, max_length=20_000)


class DocCommentAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee: str | None = Field(default=None, description="Handle or id; null takes it off")


class DocCommentOut(BaseModel):
    id: str
    doc_id: str
    parent_id: str | None = Field(description="The thread's first comment, for a reply")
    surface: Surface = Field(description="review (anchored, resolvable) or discussion")
    author_id: str
    author_handle: str | None = None
    author_kind: Literal["person", "agent", "guest"] | None = None
    body_md: str
    created_at: datetime
    edited_at: datetime | None
    mentions: list[str] = Field(default_factory=list)
    anchor_revision: int = Field(
        description="The page version or file revision current when it was written"
    )
    anchor_page_revision: int | None = Field(
        description="Immutable page save revision; null on files and legacy comments"
    )
    current_revision: int = Field(description="The doc's revision now")
    outdated: bool = Field(description="Written on an earlier revision than the current one")
    anchor_start: int | None
    anchor_end: int | None
    anchor_quote: str | None
    anchor_page: int | None
    anchor_x: float | None
    anchor_y: float | None
    suggestion_md: str | None
    accepted_at: datetime | None
    assignee_id: str | None = Field(description="Ask: the account this is addressed to")
    resolved_at: datetime | None
    resolved_by_id: str | None


class DocEventOut(BaseModel):
    id: str
    type: str
    subject_id: str | None
    actor_id: str
    source: Literal["ui", "api", "mcp", "system"]
    field: str | None
    before: Any
    after: Any
    at: datetime


class DocActivityOut(BaseModel):
    comments: list[DocCommentOut]
    events: list[DocEventOut] = Field(
        description="Revisions, saves, moves and resolved threads, oldest first; the side panel "
        "folds them into the conversation as activity lines"
    )


class AcceptOut(BaseModel):
    comment: DocCommentOut
    doc: DocOut


def _out(session: Any, c: Any) -> dict:
    return doc_threads.view(session, c)


@router.get("/docs/{doc_id}/comments", response_model=list[DocCommentOut])
def list_comments(doc_id: str, _: ReadAuth, session: DbSession) -> list[dict]:
    """Every comment on the doc, oldest first; threads are the rows sharing a `parent_id`."""
    doc = doc_threads.docs_svc.get_doc(session, doc_id)
    return doc_threads.views(session, doc, doc_threads.comments_of(session, doc_id))


@router.post(
    "/docs/{doc_id}/comments", status_code=status.HTTP_201_CREATED, response_model=DocCommentOut
)
def add_comment(doc_id: str, body: DocCommentIn, auth: WriteAuth, session: DbSession) -> dict:
    """Comment on a page or file. With `anchor` it starts a review thread (a quoted span on a
    page; a page number and/or a spot on a file); without, it is discussion. `parent_id`
    replies in a thread. `suggestion_md` proposes replacement text for the quote (pages).
    `assignee` makes it an Ask. `@handle` mentions and the Ask reach the inbox and webhooks."""
    fields = body.model_dump(exclude_none=True)
    if body.anchor is not None:
        fields["anchor"] = body.anchor.model_dump(exclude_none=True)
    return _out(session, doc_threads.add_comment(session, auth.actor, doc_id, **fields))


@router.get("/docs/{doc_id}/activity", response_model=DocActivityOut)
def activity(
    doc_id: str,
    _: ReadAuth,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict:
    """The side panel's feed: the doc's comments and the events folded in as activity lines."""
    return doc_threads.activity(session, doc_id, limit=limit)


@router.patch("/doc-comments/{comment_id}", response_model=DocCommentOut)
def edit_comment(
    comment_id: str, body: DocCommentPatch, auth: WriteAuth, session: DbSession
) -> dict:
    """Edit your own comment."""
    return _out(
        session, doc_threads.edit_comment(session, auth.actor, comment_id, body_md=body.body_md)
    )


@router.post("/doc-comments/{comment_id}/resolve", response_model=DocCommentOut)
def resolve(comment_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Close a review thread or an Ask (the thread's first comment)."""
    return _out(session, doc_threads.resolve_comment(session, auth.actor, comment_id))


@router.post("/doc-comments/{comment_id}/reopen", response_model=DocCommentOut)
def reopen(comment_id: str, auth: WriteAuth, session: DbSession) -> dict:
    return _out(session, doc_threads.reopen_comment(session, auth.actor, comment_id))


@router.post("/doc-comments/{comment_id}/assign", response_model=DocCommentOut)
def assign(comment_id: str, body: DocCommentAssignIn, auth: WriteAuth, session: DbSession) -> dict:
    """Address the comment to an account (an Ask), or take it off (`assignee: null`)."""
    return _out(session, doc_threads.assign_comment(session, auth.actor, comment_id, body.assignee))


@router.post("/doc-comments/{comment_id}/accept", response_model=AcceptOut)
def accept(comment_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Apply a review comment's suggestion to the page (an ordinary save, so page history has
    it) and resolve the thread."""
    c, doc = doc_threads.accept_suggestion(session, auth.actor, comment_id)
    return {"comment": _out(session, c), "doc": out(session, doc)}
