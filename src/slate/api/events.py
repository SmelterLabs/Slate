"""The event log, read two ways: a live SSE stream and a polling page.

    GET /api/v1/events/stream?since=&type=&item_id=   (SSE; token in header or ?token=)
    GET /api/v1/events?since=&type=&item_id=&limit=   (polling fallback; header only)

Both return `service.core.event_payload()` dicts in event-id order. Event ids are assigned,
committed and published in one order (`core.apply_change` holds a write lock across all
three), so "every event with id > cursor" is a complete, duplicate-free answer whether it
comes from the table or from the bus. The stream relies on that:

1. subscribe to the bus first, so nothing published from now on can be missed;
2. replay `id > since` from the table (or, with no `since`, take the newest id as cursor);
3. go live: skip any bus message with `id <= cursor` (it was in the replay), send the rest
   and advance the cursor;
4. if the subscriber's queue overflowed (`sub.dropped`), replay `id > cursor` from the table
   again instead of trusting the queue.

`?token=` exists because a browser's EventSource cannot set headers. It goes through the same
`resolve_token()` path, and `install()` adds a filter that redacts it from uvicorn's access log.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, replace
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    status,
)
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool

from slate import db
from slate.api.auth import DbSession, ReadAuth, WriteAuth, _unauthorized
from slate.api.schemas import ItemOut
from slate.models import Account, AccountKind, Event, Token
from slate.presence import presence
from slate.service import access, accounts, items, revert
from slate.service.bus import bus
from slate.service.core import event_payload

router = APIRouter(tags=["events"])

CURSOR_PATTERN = r"^[0-9A-Za-z]{1,12}$"
PING_SECONDS = 15
RETRY_MS = 2000  # EventSource reconnect delay we ask browsers for
REPLAY_PAGE = 1000

Cursor = Annotated[
    str | None,
    Query(pattern=CURSOR_PATTERN, description="Event id; return events after it. `0` = all."),
]
Types = Annotated[str | None, Query(description="Comma-separated event types, e.g. item.updated")]
ItemId = Annotated[str | None, Query(max_length=12)]


Actor = Annotated[
    str | None, Query(max_length=32, description="Only events by this account (handle or id)")
]
NO_MATCH = "-"  # an actor_id no account has: an unknown handle matches nothing


@dataclass(frozen=True)
class Filters:
    types: frozenset[str] | None = None
    item_id: str | None = None
    actor_id: str | None = None
    actor_kind: str | None = None  # person | agent (polling only)

    @classmethod
    def parse(cls, type: str | None, item_id: str | None, actor_id: str | None = None) -> Filters:
        types = frozenset(t.strip() for t in (type or "").split(",") if t.strip())
        return cls(types or None, item_id, actor_id)

    def matches(self, payload: dict[str, Any]) -> bool:
        if self.types is not None and payload["type"] not in self.types:
            return False
        if self.actor_id is not None and payload["actor_id"] != self.actor_id:
            return False
        return self.item_id is None or payload["item_id"] == self.item_id


def resolve_actor(session: Session, actor: str | None) -> str | None:
    """A handle or id -> account id; None stays None; unknown -> `NO_MATCH`."""
    if not actor:
        return None
    ref = actor.strip()
    found = session.scalar(
        select(Account.id).where(or_(Account.id == ref, Account.handle == ref.lower()))
    )
    return found or NO_MATCH


def read_events(
    session: Session, since: str | None, filters: Filters, limit: int, *, tail: bool = False
) -> list[dict[str, Any]]:
    """Events after `since`, oldest first; `tail` takes the newest `limit` instead of the
    oldest (still returned oldest first)."""
    q = select(Event).order_by(Event.id.desc() if tail else Event.id).limit(limit)
    if since is not None:
        q = q.where(Event.id > since)
    if filters.actor_kind is not None:
        q = q.where(
            Event.actor_id.in_(
                select(Account.id).where(Account.kind == AccountKind(filters.actor_kind))
            )
        )
    if filters.types is not None:
        q = q.where(Event.type.in_(filters.types))
    if filters.item_id is not None:
        q = q.where(Event.item_id == filters.item_id)
    if filters.actor_id is not None:
        q = q.where(Event.actor_id == filters.actor_id)
    rows = access.guest_payloads(session, [event_payload(ev) for ev in session.scalars(q)])
    return rows[::-1] if tail else rows


# --- polling -------------------------------------------------------------------------------


class EventOut(BaseModel):
    id: str
    type: str
    item_id: str | None
    subject_id: str | None
    actor_id: str | None
    source: str
    field: str | None
    before: Any
    after: Any
    session_ref: str | None
    at: str


class EventPage(BaseModel):
    events: list[EventOut]
    next_since: str | None  # pass back as `since` for the next page
    has_more: bool


def poll(
    session: Session, since: str | None, filters: Filters, limit: int, *, tail: bool = False
) -> dict[str, Any]:
    """One page of `/events` (REST and MCP `slate_events`)."""
    if tail:
        page = read_events(session, since, filters, limit, tail=True)
        return {"events": page, "next_since": page[-1]["id"] if page else since, "has_more": False}
    rows = read_events(session, since, filters, limit + 1)
    page = rows[:limit]
    return {
        "events": page,
        "next_since": page[-1]["id"] if page else since,
        "has_more": len(rows) > limit,
    }


@router.get("/events", response_model=EventPage)
def list_events(
    _auth: ReadAuth,
    session: DbSession,
    since: Cursor = None,
    type: Types = None,
    item_id: ItemId = None,
    actor: Actor = None,
    actor_kind: Annotated[
        Literal["person", "agent"] | None, Query(description="Only events by agents (or people)")
    ] = None,
    tail: Annotated[
        bool, Query(description="The newest `limit` events instead of the oldest after since")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    """Events after `since`, oldest first. The polling fallback for agents without a doorbell.
    `actor` (handle or id) keeps one account's events: a per-agent audit trail. `tail=true`
    returns the newest `limit` (what just happened), still oldest first."""
    filters = replace(
        Filters.parse(type, item_id, resolve_actor(session, actor)), actor_kind=actor_kind
    )
    return poll(session, since, filters, limit, tail=tail)


@router.post("/events/{event_id}/revert", response_model=ItemOut, status_code=200)
def revert_event(
    event_id: Annotated[str, Path(pattern=CURSOR_PATTERN)], auth: WriteAuth, session: DbSession
) -> dict[str, Any]:
    """Undo one recorded item change by issuing its inverse as you: an `item.updated` on a
    field (title, priority, dates, waiting on, description, tags...), `item.status_changed`,
    `item.assigned` or `item.moved`. The log is append-only, so this writes a new event (its
    `session_ref` is `revert:<event id>`) through the same access checks as a PATCH. 409 when
    the field has changed again since that event (nothing is written); 422 for any other
    event type."""
    item = revert.revert_event(session, auth.actor, event_id)
    return items.views(session, [item])[0]


# --- stream --------------------------------------------------------------------------------


def stream_auth(
    request: Request,
    token: Annotated[
        str | None,
        Query(description="For EventSource, which cannot set headers. Prefer the header."),
    ] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> access.EventGate:
    """Read access from a bearer header, legacy query token, or HttpOnly browser session.

    Runs before response start so failures are plain 401/403. Uses a short session rather
    than keeping a request-scoped database session open for the life of the stream.
    """
    scheme, _, header_token = (authorization or "").partition(" ")
    if authorization:
        if scheme.lower() != "bearer" or not header_token:
            raise _unauthorized("missing bearer token")
        plaintext = header_token.strip()
    elif token:
        plaintext = token
    else:
        plaintext = request.cookies.get("slate_session", "")
    with db.SessionLocal() as session:
        principal = accounts.resolve_token(session, plaintext)
        if (
            principal
            and not authorization
            and not token
            and not session.get(Token, principal.token_id).browser_session
        ):
            principal = None
        if principal is None:
            raise _unauthorized("invalid or revoked token")
        if not principal.has("read"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="token lacks 'read' scope")
        gate = access.EventGate(principal.account.id, principal.token_id)
        gate.load(session)
    return gate


def _read_page(since: str, filters: Filters, gate: access.EventGate | None = None) -> list[dict]:
    with db.SessionLocal() as session:
        if gate is not None:
            gate.load(session)
            if gate.denied:
                return []
            if gate.access is not None:
                access.attach(session, gate.access)
        return read_events(session, since, filters, REPLAY_PAGE)


def _gate_check(gate: access.EventGate, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Recheck credentials and current reach before every live delivery, in the threadpool."""
    with db.SessionLocal() as session:
        gate.load(session)
        return gate.project(session, payload)


def _newest_id() -> str:
    with db.SessionLocal() as session:
        return session.scalar(select(func.max(Event.id))) or ""


def _sse(payload: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(
        data=json.dumps(payload, separators=(",", ":")), event=payload["type"], id=payload["id"]
    )


async def _replay(
    since: str, filters: Filters, gate: access.EventGate | None = None
) -> AsyncIterator[dict[str, Any]]:
    while True:
        page = await run_in_threadpool(_read_page, since, filters, gate)
        for payload in page:
            yield payload
        if len(page) < REPLAY_PAGE:
            return
        since = page[-1]["id"]


async def _stream(
    since: str | None, filters: Filters, gate: access.EventGate | None = None
) -> AsyncIterator[ServerSentEvent]:
    async with streaming(gate):
        async for message in _stream_events(since, filters, gate):
            yield message


@asynccontextmanager
async def streaming(gate: access.EventGate | None) -> AsyncIterator[None]:
    """Count an open stream in the caller's presence (the Agents page's live streams)."""
    account_id = gate.account_id if gate is not None else None
    with presence.streaming(account_id) if account_id else nullcontext():
        yield


READY = None  # what `payloads` yields once the replay is over and delivery goes live


async def payloads(
    since: str | None, filters: Filters, gate: access.EventGate | None = None
) -> AsyncIterator[dict[str, Any] | None]:
    """Every event after `since` that the caller may see, in id order: the replay from the
    table, then `READY`, then live ones from the bus. Shared by `/events/stream` and the
    agent stream (`/checkin/stream`)."""
    async with bus.subscribe() as sub:
        if since is None:
            cursor = await run_in_threadpool(_newest_id)
        else:
            cursor = since
            async for payload in _replay(cursor, filters, gate):
                cursor = payload["id"]
                yield payload
        yield READY

        async for message in sub:
            if sub.dropped:
                sub.dropped = 0
                async for payload in _replay(cursor, filters, gate):
                    cursor = payload["id"]
                    yield payload
            if message["id"] <= cursor:
                continue
            cursor = message["id"]
            if gate is not None:
                projected = await run_in_threadpool(_gate_check, gate, message)
                if gate.denied or gate.narrowed:
                    return  # reconnect reauthenticates and clears revoked UI caches
                if projected is None:
                    continue
                message = projected  # never mutate the bus payload shared by other viewers
            if filters.matches(message):
                yield message


async def _stream_events(
    since: str | None, filters: Filters, gate: access.EventGate | None = None
) -> AsyncIterator[ServerSentEvent]:
    """Replay uses current read filters; live delivery checks credentials and current reach."""
    async for payload in payloads(since, filters, gate):
        if payload is READY:
            yield ServerSentEvent(comment="ready", retry=RETRY_MS)
        else:
            yield _sse(payload)


def _actor_id(actor: str | None) -> str | None:
    with db.SessionLocal() as session:
        return resolve_actor(session, actor)


@router.get(
    "/events/stream",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE stream"}},
)
async def stream_events(
    gate: Annotated[access.EventGate, Depends(stream_auth)],
    since: Cursor = None,
    type: Types = None,
    item_id: ItemId = None,
    actor: Actor = None,
    last_event_id: Annotated[str | None, Header(pattern=CURSOR_PATTERN)] = None,
) -> EventSourceResponse:
    """Live events as SSE (`id` = event id, `event` = type, `data` = payload JSON).

    Catch-up: `Last-Event-ID` (sent by EventSource on reconnect) or `since` replays every
    event after that id first. A `: ready` comment marks the switch to live delivery.
    """
    actor_id = await run_in_threadpool(_actor_id, actor) if actor else None
    return EventSourceResponse(
        _stream(last_event_id or since, Filters.parse(type, item_id, actor_id), gate),
        ping=PING_SECONDS,
        send_timeout=30,
    )


# --- access-log redaction ------------------------------------------------------------------

_TOKEN_IN_QUERY = re.compile(r"(?<=[?&]token=)[^&\s\"]*")


class RedactTokenQuery(logging.Filter):
    """Blanks `token=` query values in uvicorn access-log lines (args: addr, method, path...)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_QUERY.sub("REDACTED", a) if isinstance(a, str) else a for a in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = _TOKEN_IN_QUERY.sub("REDACTED", record.msg)
        return True


def install(app: FastAPI) -> None:
    app.include_router(router, prefix="/api/v1")
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactTokenQuery) for f in access.filters):
        access.addFilter(RedactTokenQuery())
