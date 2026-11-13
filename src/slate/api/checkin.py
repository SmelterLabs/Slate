"""/checkin: the caller's work queue and the news that concerns it, from one cursor."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field, TypeAdapter
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool

from slate import db
from slate.api import longpoll
from slate.api.auth import DbSession, ReadAuth
from slate.api.events import (
    CURSOR_PATTERN,
    PING_SECONDS,
    READY,
    RETRY_MS,
    Cursor,
    Filters,
    payloads,
    stream_auth,
    streaming,
)
from slate.api.items import parse_fields
from slate.api.schemas import ItemOut
from slate.models import Account, Item
from slate.service import access, checkin, item_cache, items, work

router = APIRouter(tags=["checkin"])

Why = Literal[
    "assigned",
    "unassigned",
    "mentioned",
    "reply",
    "waiting_on_you",
    "asked",
    "on_your_item",
    "watching",
    "reminder",
    "digest",
]


class NewsOut(BaseModel):
    """An event (the `/events` shape) plus why it is in your check-in."""

    id: str
    type: str
    item_id: str | None
    subject_id: str | None
    actor_id: str
    source: Literal["ui", "api", "mcp", "system"]
    field: str | None
    before: Any
    after: Any
    session_ref: str | None
    at: datetime
    why: Why
    item_title: str | None


class CheckinOut(BaseModel):
    queue: list[ItemOut] = Field(
        description="Open items assigned to you or waiting on you, most urgent first (the "
        "first `queue_limit`; `fields` trims each)"
    )
    queue_total: int = Field(description="How many open items you have in all")
    news: list[NewsOut] = Field(description="Others' events that concern you, oldest first")
    next_since: str | None = Field(description="Pass back as `since` next time")
    has_more: bool = Field(description="More news is waiting: call again with next_since now")


QUEUE_LIMIT = 50
_ITEM = TypeAdapter(ItemOut)


def build(session, account, since: str | None, limit: int, queue_limit: int = QUEUE_LIMIT) -> dict:
    """The check-in with the queue cut to `queue_limit`, its items as `ItemOut` JSON from the
    item cache (an agent's queue rarely changes between two check-ins)."""
    token = item_cache.cache.begin(session)
    out = checkin.checkin(session, account, since=since, limit=limit, queue_limit=queue_limit)
    out["queue"] = item_cache.render(session, out["queue"], items.views, _ITEM, token)
    return out


def dump(out: dict, wanted: set[str] | None) -> bytes:
    """JSON for a check-in, the queue's items trimmed to `wanted` fields (None: all)."""
    rest = CheckinOut.model_validate({**out, "queue": []}).model_dump_json(exclude={"queue"})
    queue = b"[" + b",".join(item_cache.trim(out["queue"], wanted)) + b"]"
    return b'{"queue":' + queue + b"," + rest[1:].encode()


@router.get("/checkin", response_model=CheckinOut)
async def get_checkin(
    auth: ReadAuth,
    session: DbSession,
    since: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Most news entries to return")] = 50,
    queue_limit: Annotated[
        int, Query(ge=0, le=1000, description="Most queue items to return (0: news only)")
    ] = QUEUE_LIMIT,
    fields: Annotated[
        str | None,
        Query(description="Comma-separated item fields for the queue (id always); default all"),
    ] = None,
    wait: Annotated[
        int,
        Query(
            ge=0,
            le=longpoll.MAX_WAIT,
            description="With since: hold the request up to this many seconds until news comes",
        ),
    ] = 0,
) -> Response:
    """What to work on and what changed for you, in one call. `queue`: your open items
    (assigned or waiting on you). `news`: other accounts' events that concern you, each with
    `why` (assigned, unassigned, mentioned, reply, waiting_on_you, on_your_item, watching,
    reminder, digest); a chat mention or thread reply carries the message in `after`. Keep
    `next_since` and pass it as `since` next time; without `since` you get the latest news.
    The queue is the most urgent `queue_limit` (50) of `queue_total`; `fields=id,title,due_at`
    keeps it small. With `since` and `wait`, an empty answer waits (long-poll) until news
    arrives or `wait` seconds pass; `/checkin/stream` is the same news pushed live."""
    wanted = parse_fields(fields)
    account = auth.account

    def attempt() -> dict | None:
        out = build(session, account, since, limit, queue_limit)
        return out if out["news"] or since is None or not wait else None

    out = await longpoll.wait_async(attempt, wait, session)
    if out is None:  # waited the whole time: the answer as it stands now
        out = await run_in_threadpool(build, session, account, since, limit, queue_limit)
    return Response(dump(out, wanted), media_type="application/json")


# --- the agent stream ----------------------------------------------------------------------


def _judge(
    gate: access.EventGate, me: Any, pool: bool, payload: dict[str, Any], hinted: set[str]
) -> list[ServerSentEvent]:
    """What one visible event means for this agent: a `news` message (it concerns you, with
    `why`), a `work` hint (a task you could take with /work/next is free now), both or none."""
    own = payload.get("actor_id") == me.id
    # Run now pressed by the agent's own account (another of its sessions, say a desktop chat
    # filing a card for its runner) still wakes the runner: nothing else would start it.
    # So does its own reconcile of an expired run: Asks that arrived while the card was fenced
    # hinted nothing (the card was not takeable then), and nothing else would wake it now.
    after_raw = payload.get("after")
    self_wake = own and (
        payload["type"] == "item.run_requested"
        or (
            payload["type"] == "item.work_finished"
            and isinstance(after_raw, dict)
            and after_raw.get("reconciled") is True
            and after_raw.get("expired") is True  # not a runner giving back a live grant
        )
    )
    if own and not self_wake and payload["type"] not in ("item.reminder", "account.digest"):
        return []
    out: list[ServerSentEvent] = []
    item_id = payload.get("item_id")
    with db.SessionLocal() as session:
        if gate.access is not None:
            access.attach(session, gate.access)
        ev = work.as_event(payload)
        assigned, watched = checkin._held(session, me.id, {item_id} if item_id else set())
        reason = checkin.why(ev, me, assigned, watched)
        if reason:
            item = session.get(Item, item_id) if item_id else None
            news = {**payload, "why": reason, "item_title": item.title if item else None}
            out.append(_message("news", news))
        after = payload.get("after") if isinstance(payload.get("after"), dict) else {}
        # A person's Clear of the agent's expired chat or doc turn, with a mention that waited
        # in it: that mention is a turn to take now, and nothing else would tell the runner.
        cleared = (
            not own
            and payload["type"] in (work.CHAT_TURN_TYPE, work.DOC_TURN_TYPE)
            and after.get("agent") == me.id
            and after.get("reconciled") is True
            and after.get("pending") is True
        )
        if cleared:
            place = (
                {"channel": after.get("channel"), "message_id": after.get("message_id")}
                if payload["type"] == work.CHAT_TURN_TYPE
                else {"doc_id": after.get("doc_id"), "comment_id": after.get("comment_id")}
            )
            out.append(
                _message(
                    "work",
                    {
                        "id": payload["id"],
                        "type": payload["type"],
                        **place,
                        "thread_id": after.get("thread_id"),
                        "queued": False,
                    },
                )
            )
        if payload["type"] == work.CHAT_WAKE_TYPE and (
            me.id in (after.get("for") or []) or me.id in (after.get("queued") or [])
        ):
            # A chat mention: the runner takes the turn with /work/next {channel, thread_id}.
            # `queued`: a turn there is already open, and this mention joins it (no new run).
            out.append(
                _message(
                    "work",
                    {
                        "id": payload["id"],
                        "type": payload["type"],
                        "channel": after.get("channel"),
                        "thread_id": after.get("thread_id"),
                        "message_id": after.get("message_id"),
                        "queued": me.id in (after.get("queued") or []),
                    },
                )
            )
        if payload["type"] == work.DOC_WAKE_TYPE and (
            me.id in (after.get("for") or []) or me.id in (after.get("queued") or [])
        ):
            # A doc mention: the runner takes the turn with /work/next {doc_id, thread_id}.
            out.append(
                _message(
                    "work",
                    {
                        "id": payload["id"],
                        "type": payload["type"],
                        "doc_id": after.get("doc_id"),
                        "thread_id": after.get("thread_id"),
                        "comment_id": after.get("comment_id"),
                        "queued": me.id in (after.get("queued") or []),
                    },
                )
            )
        if (
            item_id
            and (not own or self_wake)
            # A wake (Run now, the run time, the due time) always hints: the runner acts on it.
            and (item_id not in hinted or payload["type"] in work.WAKE_TYPES)
            and work.may_free_work(payload["type"])
            and work.is_takeable(session, me.id, item_id, pool=pool)
        ):
            hinted.add(item_id)
            hint = {"id": payload["id"], "item_id": item_id, "type": payload["type"]}
            if payload["type"] == "comment.assigned" and after.get("comment_id"):
                # An Ask, or a reply to the agent's question: the comment is its next turn.
                hint["comment_id"] = after["comment_id"]
                if after.get("reply"):
                    hint["reply"] = True
            out.append(_message("work", hint))
    return out


def _message(kind: str, data: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps(data, separators=(",", ":")), event=kind, id=data["id"])


def _me(account_id: str) -> Any:
    with db.SessionLocal() as session:
        a = session.get(Account, account_id)
        return SimpleNamespace(id=a.id, handle=a.handle)


async def _agent_stream(
    since: str | None, gate: access.EventGate, pool: bool
) -> AsyncIterator[ServerSentEvent]:
    me = await run_in_threadpool(_me, gate.account_id)
    hinted: set[str] = set()  # during the replay, one hint per task is enough
    async with streaming(gate):
        async for payload in payloads(since, Filters(), gate):
            if payload is READY:
                hinted = set()
                yield ServerSentEvent(comment="ready", retry=RETRY_MS)
                continue
            if str(payload["type"]).startswith("account.") and payload["subject_id"] == me.id:
                me = await run_in_threadpool(_me, me.id)  # a new handle changes mentions
            for message in await run_in_threadpool(_judge, gate, me, pool, payload, hinted):
                yield message
            if len(hinted) > 1000:
                hinted = set()


@router.get(
    "/checkin/stream",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE stream"}},
)
async def checkin_stream(
    gate: Annotated[access.EventGate, Depends(stream_auth)],
    since: Cursor = None,
    pool: Annotated[
        bool, Query(description="Also hint at unassigned tasks you could take (pool)")
    ] = False,
    last_event_id: Annotated[str | None, Header(pattern=CURSOR_PATTERN)] = None,
) -> EventSourceResponse:
    """Your check-in, pushed live: react instead of polling. Two SSE event names, both with
    `id` = the event id (your resume cursor; EventSource resends it as `Last-Event-ID`):
    `news` = an event that concerns you (the `/checkin` news shape, with `why`); `work` = a
    task you could take with `POST /work/next` just became free (`{id, item_id, type}`: it
    was assigned to you, unblocked, released...), or a chat mention to answer (`{id, type:
    message.mentioned_agent, channel, thread_id, message_id, queued}`: take it with `POST
    /work/next {channel, thread_id}`; `queued` means your open turn there takes it, no new
    run), or a doc mention (`{id, type: doc.mentioned_agent, doc_id, thread_id, comment_id,
    queued}`: take it with `POST /work/next {doc_id, thread_id}`). With `since` or
    `Last-Event-ID` it first replays what you missed; a `: ready` comment marks the switch to
    live. Token in the `Authorization` header (or `?token=`); you only hear about lists you can
    see."""
    return EventSourceResponse(
        _agent_stream(last_event_id or since, gate, pool), ping=PING_SECONDS, send_timeout=30
    )
