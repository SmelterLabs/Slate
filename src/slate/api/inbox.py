"""/inbox: the caller's notifications (mentions, assignments, changes on items they follow)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.service import attention, inbox

router = APIRouter(tags=["inbox"])

Reason = Literal[
    "mention",
    "chat",
    "reply",
    "assigned",
    "waiting",
    "due",
    "unblocked",
    "nudge",
    "reminder",
    "digest",
    "health",
    "access",
    "failed",
    "stalled",
    "comment",
    "status",
    "update",
    "archived",
    "moved",
    "watching",
]


class InboxEvent(BaseModel):
    id: str
    type: str
    item_id: str | None
    subject_id: str | None
    actor_id: str
    source: str
    field: str | None
    before: Any
    after: Any
    session_ref: str | None
    at: str


class InboxEntry(BaseModel):
    event: InboxEvent
    reason: Reason = Field(description="Why you are hearing about it")
    read: bool
    loudness: int = Field(ge=0, le=4, description="Notification level for the recipient")
    item_title: str | None = Field(
        description="The item's title, `#channel` for chat, or the page's title for a doc"
    )
    list_id: str | None
    channel: str | None = Field(default=None, description="Chat channel key, for chat entries")
    doc_id: str | None = Field(default=None, description="The doc page, for a doc mention")


class InboxCount(BaseModel):
    unread: int


class InboxMarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_ids: list[str] = Field(
        default_factory=list,
        max_length=inbox.MAX_ENTRIES,
        description="Entries by event id (at most as many as one inbox page)",
    )
    item_id: str | None = Field(default=None, description="Every entry about this item")
    all: bool = Field(default=False, description="Everything in the inbox up to now")


class InboxMarkOut(BaseModel):
    event_ids: list[str] | None = None
    through: str | None = None


@router.get("/inbox", response_model=list[InboxEntry])
def list_inbox(
    auth: ReadAuth,
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=inbox.MAX_ENTRIES),
    unread: bool = False,
    reason: Reason | None = None,
) -> list[dict]:
    """Your notifications from the last 30 days, newest first; cleared ones are left out."""
    return inbox.entries(session, auth.account, limit=limit, unread_only=unread, reason=reason)


class ReleasedAlert(BaseModel):
    cursor: str
    at: str
    loudness: int = Field(ge=1, le=4)
    title: str
    body: str
    item_id: str | None
    reason: Reason
    entry: InboxEntry | None


@router.get("/inbox/alerts", response_model=list[ReleasedAlert])
def list_alerts(
    auth: ReadAuth,
    session: DbSession,
    response: Response,
    after: str | None = Query(default=None, pattern=r"^(?:0|[0-9A-Za-z]{12})$"),
    limit: int = Query(default=100, ge=1, le=inbox.MAX_ENTRIES),
) -> list[dict]:
    """Released notifications in cursor order. Start without after to get X-Slate-Cursor."""
    if after is None:
        response.headers["X-Slate-Cursor"] = attention.latest_cursor(session, auth.account.id)
        return []
    alerts = attention.released(
        session, auth.account, after=after, limit=limit, token_id=auth.principal.token_id
    )
    response.headers["X-Slate-Cursor"] = alerts[-1]["cursor"] if alerts else after
    return alerts


@router.get("/inbox/count", response_model=InboxCount)
def inbox_count(auth: ReadAuth, session: DbSession) -> dict:
    return {"unread": inbox.unread_count(session, auth.account)}


@router.post("/inbox/read", response_model=InboxMarkOut)
def mark_read(body: InboxMarkIn, auth: WriteAuth, session: DbSession) -> dict:
    """Mark entries read: by event id, all entries for an item, or `all`."""
    return inbox.mark(
        session,
        auth.actor,
        cleared=False,
        event_ids=body.event_ids,
        item_id=body.item_id,
        everything=body.all,
    )


@router.post("/inbox/clear", response_model=InboxMarkOut)
def clear(body: InboxMarkIn, auth: WriteAuth, session: DbSession) -> dict:
    """Clear entries out of the inbox (they also count as read)."""
    return inbox.mark(
        session,
        auth.actor,
        cleared=True,
        event_ids=body.event_ids,
        item_id=body.item_id,
        everything=body.all,
    )
