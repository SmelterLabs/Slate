"""/search: one search over tasks, docs and chat, typed hits, access-filtered."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from slate.api.auth import DbSession, ReadAuth
from slate.service import search as search_svc

router = APIRouter(tags=["search"])


class SearchHit(BaseModel):
    kind: Literal["task", "doc", "message"]
    id: str
    title: str = Field(description="The task's title, the doc's title or the channel's name")
    snippet: str | None = Field(description="Plain text around the first match")
    where: str | None = Field(
        description="The task's list; the doc's list, drawer, section or `Workspace`; the "
        "message's channel as `#Todo` or `chat with My Agent`"
    )
    section: str | None = Field(
        default=None, description="Task and doc hits: the section name when in a section"
    )
    at: datetime = Field(description="Last change (task, doc) or when it was posted (message)")
    list_id: str | None = Field(default=None, description="Task and doc hits: the list")
    channel: str | None = Field(default=None, description="Message hits: the channel key")
    thread_id: str | None = Field(
        default=None, description="Message hits: the thread a reply is in (its first message)"
    )
    status_name: str | None = Field(default=None, description="Task hits: current status name")
    status_kind: str | None = Field(
        default=None, description="Task hits: status kind (open, active, done, cancelled)"
    )
    status_color: str | None = Field(default=None, description="Task hits: status colour hex")
    archived: bool = Field(default=False, description="Task hits: whether the task is archived")
    assignees: list[str] = Field(
        default_factory=list, description="Task hits: assignee account ids"
    )
    matched_in: str | None = Field(
        default=None, description="Where the match landed: id, title, body, or comment"
    )


class SearchOut(BaseModel):
    q: str
    hits: list[SearchHit] = Field(description="Tasks first, then docs, then chat (newest first)")


@router.get("/search", response_model=SearchOut)
def search(
    _: ReadAuth,
    session: DbSession,
    q: Annotated[str, Query(min_length=1, max_length=200, description="Words or #id to find")],
    limit: Annotated[int, Query(ge=1, le=search_svc.MAX_LIMIT)] = search_svc.DEFAULT_LIMIT,
    kind: Annotated[
        Literal["task", "doc", "message"] | None,
        Query(description="Narrow to one kind of hit"),
    ] = None,
    assignee: Annotated[
        str | None,
        Query(description="Task hits only: assignee handle or account id"),
    ] = None,
    list_id: Annotated[str | None, Query(description="Narrow to this list")] = None,
    section_id: Annotated[str | None, Query(description="Narrow to this section")] = None,
    include_closed: Annotated[
        bool, Query(description="Include done and cancelled tasks (default true)")
    ] = True,
    include_archived: Annotated[
        bool, Query(description="Include archived tasks (exact ID matches always show)")
    ] = False,
) -> dict[str, Any]:
    """Find `q` across task IDs, titles, descriptions and comments, doc IDs, titles, page text
    and file text, and chat messages, in one call. Tasks come first (exact ID, open, closed,
    then archived), then docs, then messages, each group sharing `limit` (default 30). Only
    what you can read; `%` and `_` are literal."""
    return {
        "q": q.strip(),
        "hits": search_svc.search(
            session,
            q,
            limit=limit,
            kind=kind,
            assignee=assignee,
            list_id=list_id,
            section_id=section_id,
            include_closed=include_closed,
            include_archived=include_archived,
        ),
    }
