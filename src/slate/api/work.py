"""/work: the agent work loop. Take the next task, keep it alive, report back in one call."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from slate.api import longpoll
from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.chat import MessageOut
from slate.api.doc_comments import DocCommentOut
from slate.api.schemas import (
    REPLY_FIELD,
    THREAD_FIELD,
    ActivityOut,
    AgentThreadOut,
    CommentOut,
    ItemOut,
)
from slate.service import chatwake, comments, docwake, items, reports, work
from slate.service.core import Actor
from slate.service.errors import Invalid

router = APIRouter(tags=["work"])


class NextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kinds: list[Literal["task", "chat", "doc"]] = Field(
        default_factory=lambda: ["task", "chat", "doc"],
        min_length=1,
        max_length=3,
        description="What you take: task, chat (a chat mention), doc (a page mention)",
    )
    run_id: str | None = Field(
        default=None,
        max_length=64,
        description="Proof from the original take; required to resume a specific item or chat turn",
    )

    lease_seconds: int = Field(
        default=work.DEFAULT_LEASE,
        ge=work.MIN_LEASE,
        le=work.MAX_LEASE,
        description="How long the task stays yours without a heartbeat (default 900)",
    )
    pool: bool = Field(
        default=False,
        description="Also take tasks nobody is assigned to, in lists you may edit (you are "
        "assigned while you hold one)",
    )
    list_id: str | None = Field(default=None, description="Only tasks in this list")
    section_id: str | None = Field(default=None, description="Only tasks in this section")
    tag: str | None = Field(default=None, description="Only tasks with this tag")
    item_id: str | None = Field(
        default=None,
        description="Only this task: a runner answering a `work` hint passes its item_id, so "
        "Run now runs the task that was pressed (item is null when it is not free for you)",
    )
    channel: str | None = Field(
        default=None,
        description="Only your chat turn in this channel (`general`, `list:<id>`, "
        "`section:<id>`, `agent:<id>`): a runner answering a `work` hint that carries "
        "`channel` passes it (with `thread_id`) and gets `chat`, not `item`. Without it, a "
        "chat turn is handed out once no task is free",
    )
    thread_id: str | None = Field(
        default=None,
        description="With channel: the thread (its first message's id) the mention was in; "
        "omit for the channel itself. With doc_id: the doc thread (its first comment's id); "
        "omit for a mention in the page body",
    )
    doc_id: str | None = Field(
        default=None,
        description="Only your doc turn on this page (with `thread_id`): a runner answering a "
        "`work` hint that carries `doc_id` passes it and gets `doc`, not `item`. Without it, a "
        "doc turn is handed out once no task and no chat turn is free",
    )
    resume: bool = Field(
        default=True, description="Return a task you already hold first (its lease renewed)"
    )
    note: str | None = Field(
        default=None, max_length=280, description="Badge note for the take, e.g. 'Starting'"
    )
    context: bool = Field(
        default=True, description="Include the task's last 10 comments and events"
    )
    wait: int = Field(
        default=0,
        ge=0,
        le=longpoll.MAX_WAIT,
        description="Seconds to wait for a task when none is free now (long-poll)",
    )


class LeaseOut(BaseModel):
    run_id: str | None = None
    item_id: str
    account_id: str
    claimed_at: datetime
    until: datetime = Field(description="Heartbeat before this, or the task goes back")
    from_pool: bool = Field(
        description="The take assigned you (the task had no assignee); an expired lease "
        "un-assigns you again"
    )


class ChatLeaseOut(BaseModel):
    run_id: str | None = None
    account_id: str
    claimed_at: datetime | None
    until: datetime | None = Field(
        description="Heartbeat (`POST /work/chat/heartbeat`) before this, or the turn ends"
    )


class ChatTurnOut(BaseModel):
    """A chat turn: someone @mentioned you in a channel or a thread, and your reply goes
    there (`POST /work/chat/finish {reply}`, or any post of yours in that channel or thread)."""

    channel: str = Field(description="Where you were mentioned (`list:<id>`, ...)")
    channel_name: str
    thread_id: str | None = Field(
        description="The thread the mention was in (reply there), or null for the channel"
    )
    message_id: str = Field(description="The newest message that mentioned you")
    since_id: str = Field(
        description="The oldest mention this turn answers (several may have arrived)"
    )
    message: MessageOut = Field(description="The mention itself")
    messages: list[MessageOut] = Field(
        description="What you may read for context: the thread, oldest first, or the newest "
        "top-level messages of the channel"
    )
    lease: ChatLeaseOut


class DocMentionOut(BaseModel):
    excerpt: str = Field(description="The line that mentioned you")
    by_id: str = Field(description="Who mentioned you")
    by_handle: str | None
    by_kind: str | None = Field(description="person or agent")
    comment: DocCommentOut | None = Field(
        description="The comment that mentioned you; null for a mention in the page body"
    )


class DocMayOut(BaseModel):
    reply: bool = Field(description="Answer with `POST /work/doc/finish {reply}`")
    edit: bool = Field(description="You may also edit the page (`PATCH /docs/{id}`)")


class DocTurnOut(BaseModel):
    """A doc turn: someone @mentioned you on a page or in one of its threads, and your answer
    goes there (`POST /work/doc/finish {reply}`): in the thread, or as a new discussion thread
    on the page when the mention was in the body."""

    doc_id: str
    title: str
    kind: str = Field(description="page or file")
    path: list[dict[str, Any]] = Field(description="The pages above it, top first")
    thread_id: str | None = Field(
        description="The thread the mention was in (your reply goes there), or null for the "
        "page body"
    )
    comment_id: str | None = Field(description="The newest comment that mentioned you")
    mention: DocMentionOut
    section_md: str = Field(
        description="The page text around the mention (its heading section, or around the "
        "thread's quoted span), at most 8000 characters; `GET /docs/{id}` has the whole page"
    )
    body_chars: int = Field(description="Length of the whole page body")
    comments: list[DocCommentOut] = Field(
        description="The thread, oldest first (empty for a body mention)"
    )
    may: DocMayOut
    lease: ChatLeaseOut


class NextOut(BaseModel):
    item: ItemOut | None = Field(
        description="The task you now hold; null when none is free (or when `chat` is set)"
    )
    lease: LeaseOut | None
    chat: ChatTurnOut | None = Field(
        default=None,
        description="A chat mention to answer instead of a task (`why` = mentioned): see "
        "ChatTurnOut. Reply with `POST /work/chat/finish`",
    )
    doc: DocTurnOut | None = Field(
        default=None,
        description="A page mention to answer instead of a task (`why` = mentioned): see "
        "DocTurnOut. Reply with `POST /work/doc/finish`",
    )
    resumed: bool = Field(default=False, description="You already held it")
    why: Literal["assigned", "waiting_on_you", "asked", "pool", "mentioned"] | None = Field(
        default=None,
        description="asked = you are not assigned but someone asked you something on it (see "
        "`asks`); finish done answers the taken Asks without completing the task. "
        "mentioned = a chat turn (`chat`) or a doc turn (`doc`), not a task",
    )
    context: list[ActivityOut] = Field(
        default_factory=list, description="Its last comments and events, oldest first"
    )
    asks: list[CommentOut] = Field(
        default_factory=list,
        description="Pending Asks for this take only; a work finish "
        "consumes their dispatch, not their visibility. Newer assignments wait for the next turn",
    )
    reply: CommentOut | None = Field(
        default=None,
        description="The newest open Ask for you on it written by a person: the answer to "
        "your last question (a reply on a task you reported blocked waiting on them). Send "
        "it to your session as the next turn. Check author_kind: only a person's words are "
        "the user's turn",
    )
    thread: AgentThreadOut | None = Field(
        default=None,
        description="Your own conversation for this task, as you stored it with thread on an "
        "earlier report, heartbeat or finish: continue that session",
    )
    remaining: int = Field(description="Other tasks you could take now")


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = Field(default=None, max_length=64)

    lease_seconds: int = Field(default=work.DEFAULT_LEASE, ge=work.MIN_LEASE, le=work.MAX_LEASE)
    note: str | None = Field(default=None, max_length=280, description="Update the badge note")
    progress: int | None = Field(default=None, ge=0, le=100, description="Percent done")
    url: str | None = Field(
        default=None,
        max_length=2000,
        description="http(s) link to where you are working (chat, session, log); shown on "
        "the card as Open",
    )
    thread: dict[str, Any] | None = THREAD_FIELD


class FinishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def separate_take(self):
        if self.run_id and self.next is not None:
            raise ValueError(
                "fenced finish must be separate from next; retry delivery before taking more work"
            )
        return self

    outcome: Literal["done", "review", "blocked", "release"] = Field(
        description="done completes it; review leaves a 'ready for review' badge; blocked "
        "(with waiting_on) asks someone; release gives it up (un-assigns you)"
    )
    note: str | None = Field(
        default=None, max_length=4000, description="What you did; posted as your comment"
    )
    comment: bool = Field(default=True, description="Post the note as a comment")
    progress: int | None = Field(default=None, ge=0, le=100)
    status: str | None = Field(
        default=None, description="Set this status (name or id) instead of the default"
    )
    hand_to: str | None = Field(
        default=None,
        description="Handle to assign the task to in your place (an agent picks it up next)",
    )
    waiting_on: str | None = Field(default=None, description="With blocked: who you wait on")
    url: str | None = Field(
        default=None,
        max_length=2000,
        description="With review or blocked: link to where the work is (chat, PR)",
    )
    reply: str | None = REPLY_FIELD
    thread: dict[str, Any] | None = THREAD_FIELD
    next: NextIn | None = Field(
        default=None, description="Also take your next task in the same call"
    )


class FinishOut(BaseModel):
    item: ItemOut = Field(description="The task you reported on, as it is now")
    next: NextOut | None = Field(description="Your next task, when you asked for one")


def take_next(session: Session, actor: Actor, body: NextIn) -> dict[str, Any] | None:
    """One attempt; None when nothing is free. Shared by REST, MCP and finish. Tasks first;
    a chat turn (a mention of the caller in a channel) when no task is free, then a doc turn
    (a mention on a page); only that turn when `channel` or `doc_id` is given."""
    if body.thread_id and not (body.channel or body.doc_id):
        raise Invalid("thread_id goes with channel or doc_id")
    if body.channel and body.doc_id:
        raise Invalid("channel and doc_id are separate turns: pass one")
    scope = {"pool": body.pool, "list_id": body.list_id, "section_id": body.section_id}

    def left() -> int:
        return (
            work.remaining(session, actor.account_id, tag=body.tag, **scope)
            + chatwake.remaining(session, actor.account_id)
            + docwake.remaining(session, actor.account_id)
        )

    got = None
    if not (body.channel or body.doc_id) and "task" in body.kinds:
        got = work.next_task(
            session,
            actor,
            lease=body.lease_seconds,
            resume=body.resume,
            note=body.note,
            run_id=body.run_id,
            tag=body.tag,
            item_id=body.item_id,
            **scope,
        )
    if got is None:
        if body.item_id:
            return None
        turn = None
        if not body.doc_id and "chat" in body.kinds:
            turn = chatwake.next_turn(
                session,
                actor,
                lease=body.lease_seconds,
                channel=body.channel,
                thread_id=body.thread_id,
                resume=body.resume,
                run_id=body.run_id,
            )
        if turn is None and not body.channel and "doc" in body.kinds:
            turn = docwake.next_turn(
                session,
                actor,
                lease=body.lease_seconds,
                doc_id=body.doc_id,
                thread_id=body.thread_id,
                resume=body.resume,
                run_id=body.run_id,
            )
        if turn is None:
            return None
        return {
            **nothing(),
            "chat": turn.get("chat"),
            "doc": turn.get("doc"),
            "resumed": turn["resumed"],
            "why": "mentioned",
            "remaining": left(),
        }
    got["item"] = items.views(session, [got["item"]])[0]
    got["context"] = work.context(session, got["item"]["id"]) if body.context else []
    got["asks"] = comments.comment_views(
        session, work.turn_asks(session, actor.account_id, got["item"]["id"])
    )
    by_people = [a for a in got["asks"] if a.get("author_kind") == "person"]
    got["reply"] = by_people[-1] if by_people else None
    thread = reports.thread_of(session, got["item"]["id"], actor.account_id)
    got["thread"] = {"account_id": actor.account_id, **thread} if thread else None
    got["chat"] = None
    got["doc"] = None
    got["remaining"] = left()
    return got


def nothing() -> dict[str, Any]:
    return {
        "item": None,
        "lease": None,
        "chat": None,
        "doc": None,
        "context": [],
        "remaining": 0,
    }


class ChatTurnRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = Field(default=None, max_length=64)

    channel: str = Field(description="The channel of the turn you hold")
    thread_id: str | None = Field(
        default=None, description="Its thread, when the mention was in one"
    )


class ChatHeartbeatIn(ChatTurnRef):
    lease_seconds: int = Field(default=work.DEFAULT_LEASE, ge=work.MIN_LEASE, le=work.MAX_LEASE)


class ChatFinishIn(ChatTurnRef):
    outcome: Literal["done", "release"] = Field(
        description="done = answered (post `reply`, or you already posted there); release = "
        "give the turn up with nothing posted (a failed run). Nothing is retried either way: "
        "a new mention starts a new turn"
    )
    reply: str | None = Field(
        default=None,
        max_length=20_000,
        description="Your answer, posted as your message in the channel (in the thread when "
        "the mention was in one)",
    )
    note: str | None = Field(
        default=None, max_length=4000, description="Why, for the history (never posted)"
    )


class ChatFinishOut(BaseModel):
    message: MessageOut | None = Field(description="The reply as posted, when there was one")
    pending: bool = Field(
        description="A mention arrived while this turn was open: your next `/work/next` for "
        "the channel hands it to you"
    )


class DocTurnRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = Field(default=None, max_length=64)

    doc_id: str = Field(description="The doc of the turn you hold")
    thread_id: str | None = Field(
        default=None, description="Its thread, when the mention was in one"
    )


class DocHeartbeatIn(DocTurnRef):
    lease_seconds: int = Field(default=work.DEFAULT_LEASE, ge=work.MIN_LEASE, le=work.MAX_LEASE)


class DocFinishIn(DocTurnRef):
    outcome: Literal["done", "release"] = Field(
        description="done = answered (post `reply`); release = give the turn up with nothing "
        "posted (a failed run). Nothing is retried either way: a new mention starts a new turn"
    )
    reply: str | None = Field(
        default=None,
        max_length=20_000,
        description="Your answer, posted as your comment in the thread (a new discussion "
        "thread on the page when the mention was in the body)",
    )
    note: str | None = Field(
        default=None, max_length=4000, description="Why, for the history (never posted)"
    )


class DocFinishOut(BaseModel):
    comment: DocCommentOut | None = Field(description="The reply as posted, when there was one")
    pending: bool = Field(
        description="A mention arrived while this turn was open: your next `/work/next` for "
        "the doc hands it to you"
    )


@router.post("/work/next", response_model=NextOut)
async def next_task(body: NextIn, auth: WriteAuth, session: DbSession) -> dict[str, Any]:
    """Take your next task in one call: the best open task assigned to you or waiting on you
    (with `pool`, also unassigned ones) that is not blocked by an open task, not started
    later, not held by another agent and not waiting on a person. Order: priority, due
    date, board position. An agent's assigned task with a due date
    waits for its run time (06:00 workspace time on the day for a date-only due date, else
    the due time) unless someone pressed Run now. `item_id` takes that task or nothing. It is yours
    (live 'working' badge, assigned) for `lease_seconds`; heartbeat to keep it, finish to
    report back. With `wait`, holds the request up to that many seconds until a task frees
    up. `item` is null when none is free.

    When no task is free, a chat turn comes instead (`chat`, `why: mentioned`): someone
    @mentioned you in a channel or thread (`message.mentioned_agent`, a `work` hint with
    `channel`, `thread_id`). `channel` (+ `thread_id`) takes exactly that turn. Reply with
    `POST /work/chat/finish`. After chat, a doc turn (`doc`): someone @mentioned you on a page
    or in one of its threads (`doc.mentioned_agent`, a hint with `doc_id`, `thread_id`);
    `doc_id` (+ `thread_id`) takes that turn. Reply with `POST /work/doc/finish`. `kinds`
    limits what you take."""
    got = await longpoll.wait_async(
        lambda: take_next(session, auth.actor, body), body.wait, session
    )
    return got or nothing()


@router.get("/work", response_model=list[LeaseOut])
def my_leases(auth: ReadAuth, session: DbSession) -> list[dict]:
    """The tasks you hold now, soonest lease to run out first."""
    return work.held_by(session, auth.account.id)


class ReconcileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=64)
    state: Literal["stopped", "completed"]
    evidence: str = Field(min_length=1, max_length=2000)


class ChatReconcileIn(ReconcileIn):
    channel: str
    thread_id: str | None = None


class DocReconcileIn(ReconcileIn):
    doc_id: str
    thread_id: str | None = None


@router.post("/work/chat/reconcile")
def chat_reconcile(body: ChatReconcileIn, auth: WriteAuth, session: DbSession) -> dict:
    """Confirm remote termination with retained run proof; local process exit is not evidence."""
    return chatwake.reconcile(session, auth.actor, **body.model_dump())


@router.post("/work/doc/reconcile")
def doc_reconcile(body: DocReconcileIn, auth: WriteAuth, session: DbSession) -> dict:
    """Confirm remote termination of a doc turn with retained run proof (after it expired)."""
    return docwake.reconcile(session, auth.actor, **body.model_dump())


@router.post("/work/{item_id}/reconcile")
def reconcile(item_id: str, body: ReconcileIn, auth: WriteAuth, session: DbSession) -> dict:
    """Confirm remote termination with retained run proof; unlock without completing the task."""
    return work.reconcile(session, auth.actor, item_id, **body.model_dump())


@router.post("/work/chat/heartbeat", response_model=ChatLeaseOut)
def chat_heartbeat(body: ChatHeartbeatIn, auth: WriteAuth, session: DbSession) -> dict:
    """Keep holding a chat turn (`chat` from `/work/next`): the lease runs `lease_seconds`
    from now. 409 when you hold no open turn there (it ended or ran out)."""
    row = chatwake.heartbeat(
        session,
        auth.actor,
        body.channel,
        body.thread_id,
        lease=body.lease_seconds,
        run_id=body.run_id,
    )
    return chatwake.lease_view(row)


@router.post("/work/chat/finish", response_model=ChatFinishOut)
def chat_finish(body: ChatFinishIn, auth: WriteAuth, session: DbSession) -> dict:
    """End a chat turn. `done` with `reply` posts the reply as your message in the channel
    (in the thread when the mention was in one): that is the answer, and it reaches the board
    live like any message. `release` (or `done` with no reply) ends the turn with nothing
    posted. Any message you post in that channel or thread yourself ends the turn too, so an
    agent answering through `POST /channels/{key}/messages` or MCP `slate_chat post` needs no
    finish call. 409 when you hold no open turn there."""
    return chatwake.finish(
        session,
        auth.actor,
        body.channel,
        body.thread_id,
        outcome=body.outcome,
        run_id=body.run_id,
        reply=body.reply,
        note=body.note,
    )


@router.post("/work/doc/heartbeat", response_model=ChatLeaseOut)
def doc_heartbeat(body: DocHeartbeatIn, auth: WriteAuth, session: DbSession) -> dict:
    """Keep holding a doc turn (`doc` from `/work/next`): the lease runs `lease_seconds` from
    now. 409 when you hold no open turn there (it ended or ran out)."""
    row = docwake.heartbeat(
        session,
        auth.actor,
        body.doc_id,
        body.thread_id,
        lease=body.lease_seconds,
        run_id=body.run_id,
    )
    return docwake.lease_view(row)


@router.post("/work/doc/finish", response_model=DocFinishOut)
def doc_finish(body: DocFinishIn, auth: WriteAuth, session: DbSession) -> dict:
    """End a doc turn, the same contract as `/work/chat/finish`. `done` with `reply` posts the
    reply as your comment in the thread the mention was in, or as a new discussion thread on
    the page when the mention was in the body; it reaches the board live like any comment.
    `release` (or `done` with no reply) ends the turn with nothing posted. An identical retry
    returns the first result. 409 when you hold no open turn there."""
    return docwake.finish(
        session,
        auth.actor,
        body.doc_id,
        body.thread_id,
        outcome=body.outcome,
        run_id=body.run_id,
        reply=body.reply,
        note=body.note,
    )


@router.post("/work/{item_id}/heartbeat", response_model=LeaseOut)
def heartbeat(item_id: str, body: HeartbeatIn, auth: WriteAuth, session: DbSession) -> dict:
    """Keep holding the task: the lease runs `lease_seconds` from now. `note` or `progress`
    also update the card's badge live. 409 means you lost it (the lease ran out and another
    agent took the task): stop working on it."""
    row = work.heartbeat(
        session,
        auth.actor,
        item_id,
        lease=body.lease_seconds,
        run_id=body.run_id,
        note=body.note,
        progress=body.progress,
        url=body.url,
        thread=body.thread,
    )
    return work.lease_view(row)


@router.post("/work/{item_id}/finish", response_model=FinishOut)
async def finish(item_id: str, body: FinishIn, auth: WriteAuth, session: DbSession) -> dict:
    """Report back in one round trip, all in one transaction: the outcome (done, review,
    blocked, release), a note posted as your comment, a `status`, and
    `hand_to` to pass the task to another account. Your lease ends. With `next`, the same
    call also takes your next task (and may `wait` for one). Agent finishes consume only Ask
    assignments present at take, so the same open Ask cannot repeatedly dispatch done,
    review, blocked or released work. Asks remain visible and explicitly resolvable; only
    asked-only agent done resolves its taken Asks (without completing the task). New or
    reassigned Asks during the run remain pending. Human finishes never resolve Asks."""

    def report() -> dict:
        item = work.finish(
            session,
            auth.actor,
            item_id,
            **body.model_dump(exclude={"next"}),
        )
        return items.views(session, [item])[0]

    view = await run_in_threadpool(report)
    nxt = None
    if body.next is not None and not session.info.get("work_finish_replayed"):
        n = body.next
        nxt = await longpoll.wait_async(lambda: take_next(session, auth.actor, n), n.wait, session)
        nxt = nxt or nothing()
    return {"item": view, "next": nxt}
