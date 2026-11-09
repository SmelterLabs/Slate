"""/inbox: the caller's notifications (mentions, assignments, changes on items they follow)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.service import inbox

router = APIRouter(tags=["inbox"])

Reason = Literal[
    "mention",
    "reply",
    "assigned",
    "waiting",
    "due",
    "unblocked",
    "nudge",
    "reminder",
    "digest",
    "health",
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

    event_ids: list[str] = Field(default_factory=list, description="Entries by event id")
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
