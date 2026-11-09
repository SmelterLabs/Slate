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
    at: datetime = Field(description="Last change (task, doc) or when it was posted (message)")
    list_id: str | None = Field(default=None, description="Task and doc hits: the list")
    channel: str | None = Field(default=None, description="Message hits: the channel key")
    thread_id: str | None = Field(
        default=None, description="Message hits: the thread a reply is in (its first message)"
    )


class SearchOut(BaseModel):
    q: str
    hits: list[SearchHit] = Field(description="Tasks first, then docs, then chat (newest first)")


@router.get("/search", response_model=SearchOut)
def search(
    _: ReadAuth,
    session: DbSession,
    q: Annotated[str, Query(min_length=1, max_length=200, description="Words to find")],
    limit: Annotated[int, Query(ge=1, le=search_svc.MAX_LIMIT)] = search_svc.DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Find `q` in task titles and descriptions, doc titles, page text and the text inside
    files, and chat messages, in one call. Tasks come first, then docs, then messages, each
    group sharing `limit` (default 30). Only what you can read; `%` and `_` are literal."""
    return {"q": q.strip(), "hits": search_svc.search(session, q, limit=limit)}
