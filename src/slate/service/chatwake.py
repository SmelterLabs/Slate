"""A chat @mention starts a runner-backed agent (docs/chat.md).

A person who @mentions an agent in a channel or a thread expects an answer there, the way an
Ask on a card gets one (service/comments.py, service/work.py). The chat side of that:

- `on_mentioned` (called by chat.post and chat.edit inside their change): for each mentioned
  account that is an enabled agent with wake mode `push` and may post in the channel
  (`access.compute`, so an agent mentioned where it cannot read is not woken), one `ChatWake`
  row per (agent, channel, thread) and one `message.mentioned_agent` event (`after.for` = the
  agents woken, `after.queued` = those whose turn there is already open: the new mention folds
  into it, no second run). A mention by an agent never wakes another agent (no loops).
- `next_turn` (REST `POST /work/next {channel, thread_id}`, or a generic `/work/next` once no
  task is free): the runner takes the turn with a lease, like a task. `message.agent_turn`
  `state: started`. The turn carries the channel, the thread, the mention and the recent
  messages the agent may read; the runner feeds that to the agent.
- The answer is the ordinary chat `post`: a post by the agent in that channel or thread ends its
  open turn (`message.agent_turn` `state: done`, `reply_id`). `POST /work/chat/finish` posts the
  `reply` the same way, or ends the turn without a word (`release`, for a failed run: nothing
  is written into a collaborator's chat by mistake, and nothing is retried; the person mentions
  the agent again). A mention that arrived while the turn was open makes the row pending again
  (`after.pending` true) and the next take answers it.
- `heartbeat` moves the lease without an event (`until` is bookkeeping, core.BOOKKEEPING);
  `expire` (scheduler hook) ends a turn whose lease ran out (`state: expired`, actor `slate`).

Agent machinery stays history: the wake, the take and the end are events; the row is the
lease. Rate limits are the token's as for any call. Slate knows nothing about what runs.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, ChatWake, Message, Role, utcnow
from slate.service import access, chat, work
from slate.service.core import Actor, apply_change
from slate.service.errors import Conflict, Invalid, NotFound

# Top-level messages handed to the agent as context for a channel-level mention (a thread hands
# over the whole thread).
TURN_MESSAGES = 20
OUTCOMES = ("done", "release")
WAKE_EVENT = "message.mentioned_agent"
TURN_EVENT = "message.agent_turn"


def _key(thread_id: str | None) -> str:
    return thread_id or ""


def _can_post(session: Session, agent: Account, channel: str) -> bool:
    """May this agent read and post in the channel? Computed for the agent itself, not for
    whoever is posting now (whose filter would otherwise shape the answer)."""
    with access.unrestricted(session):
        a = access.compute(session, agent)
    return a.channel_level(channel) >= access.COMMENT


def _reaches(poster: Account, agent: Account, msg: Message) -> bool:
    """A project's guests talk with the project's own agent, never a workspace agent (a guest's
    `@my-agent` wakes nothing); a members-only message never wakes a guest agent, which could not
    read it."""
    if poster.role == Role.guest and chat.workspace_agent(agent):
        return False
    return not (msg.members_only and agent.role == Role.guest)


# --- the wake -------------------------------------------------------------------------------


def on_posted(
    cx: Any, session: Session, actor: Actor, msg: Message, mentions: list[str], ch: chat.Channel
) -> None:
    """After `message.posted` in the same change: a person's mentions wake agents; an agent's
    post answers its open turn there."""
    poster = session.get(Account, actor.account_id)
    if poster is not None and poster.kind == AccountKind.agent:
        _answered(cx, session, actor, msg)
        return
    on_mentioned(cx, session, actor, msg, mentions, ch)


def on_mentioned(
    cx: Any, session: Session, actor: Actor, msg: Message, handles: list[str], ch: chat.Channel
) -> None:
    """Wake every push agent among `handles` that may post in `ch`, unless the poster is an
    agent. One `message.mentioned_agent` event when anyone is woken or queued."""
    if not handles:
        return
    poster = session.get(Account, actor.account_id)
    if poster is None or poster.kind == AccountKind.agent:
        return
    agents = [
        a
        for a in session.scalars(
            select(Account).where(
                Account.handle.in_(handles),
                Account.kind == AccountKind.agent,
                Account.disabled_at.is_(None),
            )
        )
        if (a.wake or "push") == "push"
        and _can_post(session, a, ch.key)
        and _reaches(poster, a, msg)
    ]
    if not agents:
        return
    agents.sort(key=lambda a: a.id)
    thread_key = _key(msg.parent_id)
    now = utcnow()
    # Every read first: a query after the first row changes would flush it uncovered.
    rows = {
        r.account_id: r
        for r in session.scalars(
            select(ChatWake).where(
                ChatWake.account_id.in_([a.id for a in agents]),
                ChatWake.channel == ch.key,
                ChatWake.thread_id == thread_key,
            )
        )
    }
    woken: list[str] = []
    queued: list[str] = []
    covers: list[ChatWake] = []
    for a in agents:
        row = rows.get(a.id)
        if row is None:
            row = ChatWake(
                account_id=a.id,
                channel=ch.key,
                thread_id=thread_key,
                message_id=msg.id,
                since_id=msg.id,
                created_at=now,
            )
            session.add(row)
            woken.append(a.id)
        elif row.claimed_at is not None:
            # A turn is open there: the mention joins it; the next take answers it if the run
            # ends without having seen it.
            row.message_id = msg.id
            if row.taken_id is not None and row.since_id <= row.taken_id:
                row.since_id = msg.id
            queued.append(a.id)
        else:
            # Pending (or a lease nobody swept yet): one turn answers both mentions.
            row.message_id = msg.id
            row.claimed_at = None
            row.until = None
            row.taken_id = None
            woken.append(a.id)
        covers.append(row)
    cx.record(
        WAKE_EVENT,
        subject_id=msg.id,
        after={
            "channel": ch.key,
            "channel_name": ch.name,
            "thread_id": msg.parent_id,
            "message_id": msg.id,
            "for": woken,
            "queued": queued,
        },
        covers=covers,
    )


# --- the turn -------------------------------------------------------------------------------


def _channel_name(session: Session, key: str) -> str | None:
    try:
        return chat.parse_channel(session, key).name
    except (NotFound, Invalid):
        return None


def lease_view(row: ChatWake) -> dict[str, Any]:
    return {
        "account_id": row.account_id,
        "claimed_at": row.claimed_at,
        "until": row.until,
        "run_id": row.run_id,
    }


def turn_view(session: Session, row: ChatWake) -> dict[str, Any]:
    """What the runner hands the agent: where, which mention, and the recent messages it may
    read (the thread, or the newest top-level messages of the channel). `message_id` is the
    mention this turn covers (`taken_id` once taken): a mention that arrives while the turn is
    open is in `messages` already, and is the next turn's own `message_id` if the reply does
    not cover it."""
    ch = chat.parse_channel(session, row.channel)
    msg = chat.get_message(session, row.taken_id or row.message_id)
    if row.thread_id:
        msgs = chat.thread(session, row.thread_id)
    else:
        msgs = chat.messages(session, row.channel, limit=TURN_MESSAGES)
    views = {v["id"]: v for v in chat.message_views(session, [*msgs, msg])}
    return {
        "channel": ch.key,
        "channel_name": ch.name,
        "thread_id": row.thread_id or None,
        "message_id": msg.id,
        "since_id": row.since_id,
        "message": views[msg.id],
        "messages": [views[m.id] for m in msgs],
        "lease": lease_view(row),
    }


def _mine(account_id: str, channel: str | None, thread_id: str | None) -> Any:
    q = select(ChatWake).where(ChatWake.account_id == account_id)
    if channel:
        q = q.where(ChatWake.channel == channel, ChatWake.thread_id == _key(thread_id))
    return q


def _pending(q: Any, now: datetime) -> Any:
    return q.where(ChatWake.claimed_at.is_(None)).order_by(ChatWake.since_id)


def remaining(session: Session, account_id: str, *, now: datetime | None = None) -> int:
    """How many chat turns the account could take now."""
    now = now or utcnow()
    sub = (
        _pending(_mine(account_id, None, None), now)
        .order_by(None)
        .with_only_columns(ChatWake.account_id)
        .subquery()
    )
    return session.scalar(select(func.count()).select_from(sub)) or 0


def next_turn(
    session: Session,
    actor: Actor,
    *,
    lease: int | None = None,
    channel: str | None = None,
    thread_id: str | None = None,
    resume: bool = True,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Take the agent's next chat turn (that channel and thread, when given): `{chat, resumed}`,
    or None. With `resume`, a turn it already holds comes back first with its lease renewed."""
    if thread_id and not channel:
        raise Invalid("thread_id goes with channel")
    seconds = work.lease_seconds(lease)
    now = now or utcnow()
    me = actor.account_id
    base = _mine(me, channel, thread_id)
    with apply_change(session, actor) as cx:
        if run_id:
            if not resume or not channel:
                raise Invalid("run_id resume requires resume=true and channel")
            held = _held(session, actor, channel, thread_id, now, run_id)
            held.until = now + timedelta(seconds=seconds)
            return {"chat": turn_view(session, held), "resumed": True}
        for row in session.scalars(
            _pending(base, now).execution_options(populate_existing=True)
        ).all():
            name = _channel_name(session, row.channel)
            if name is None:
                continue
            access.check_object(session, row)
            row.run_id = secrets.token_urlsafe(32)
            row.claimed_at = now
            row.until = now + timedelta(seconds=seconds)
            row.taken_id = row.message_id
            cx.record(
                TURN_EVENT,
                subject_id=row.message_id,
                after=_turn_after(row, name, "started"),
                covers=[row],
            )
            return {"chat": turn_view(session, row), "resumed": False}
    return None


def _turn_after(row: ChatWake, name: str | None, state: str, **extra: Any) -> dict[str, Any]:
    return {
        "channel": row.channel,
        "channel_name": name,
        "thread_id": row.thread_id or None,
        "message_id": row.taken_id or row.message_id,
        "agent": row.account_id,
        "state": state,
        "pending": False,
        **extra,
    }


def _held(
    session: Session,
    actor: Actor,
    channel: str,
    thread_id: str | None,
    now: datetime,
    run_id: str | None = None,
) -> ChatWake:
    row = session.get(
        ChatWake, (actor.account_id, channel, _key(thread_id)), populate_existing=True
    )
    if row is None or row.claimed_at is None or row.until is None or row.until < now:
        where = f"{channel}" + (f" thread {thread_id}" if thread_id else "")
        raise Conflict(f"you hold no open turn in {where}: take it with /work/next first")
    if not run_id or not row.run_id or not secrets.compare_digest(run_id, row.run_id):
        raise Conflict("current run_id proof required for chat turn")
    access.check_object(session, row)
    ch = chat.parse_channel(session, channel)
    if ch.archived:
        raise Conflict("channel is archived; stop execution")
    return row


def heartbeat(
    session: Session,
    actor: Actor,
    channel: str,
    thread_id: str | None = None,
    *,
    lease: int | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> ChatWake:
    """Keep the turn: the lease runs `lease` seconds from now. No event (bookkeeping)."""
    seconds = work.lease_seconds(lease)
    now = now or utcnow()
    with apply_change(session, actor):
        row = _held(session, actor, channel, thread_id, now, run_id)
        row.until = now + timedelta(seconds=seconds)
    return row


def _end(cx: Any, session: Session, row: ChatWake, state: str, **extra: Any) -> bool:
    """End the turn: the row goes, unless a mention arrived while it was open (then it is
    pending again, `after.pending` true, and the next take answers it). One event."""
    name = _channel_name(session, row.channel)  # read before the row changes
    pending = row.taken_id is not None and row.message_id > row.taken_id
    after = _turn_after(row, name, state, **extra)
    after["pending"] = pending
    if pending:
        row.claimed_at = None
        row.until = None
        row.taken_id = None
        row.run_id = None
    else:
        cx.session.delete(row)
    cx.record(TURN_EVENT, subject_id=after["message_id"], after=after, covers=[row])
    return pending


def _answered(cx: Any, session: Session, actor: Actor, msg: Message) -> None:
    row = session.get(ChatWake, (actor.account_id, msg.channel, _key(msg.parent_id)))
    if row is None or row.claimed_at is None or row.run_id is not None:
        return
    _end(cx, session, row, "done", reply_id=msg.id)


def finish(
    session: Session,
    actor: Actor,
    channel: str,
    thread_id: str | None = None,
    *,
    outcome: str,
    run_id: str | None = None,
    reply: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """End the turn: `done` with `reply` posts it in the channel or thread (the ordinary chat
    post, which is what answers the mention); `done` without one, or `release`, ends it with
    nothing posted. Returns `{message, pending}`."""
    if outcome not in OUTCOMES:
        raise Invalid(f"outcome must be one of {', '.join(OUTCOMES)}")
    now = now or utcnow()
    note = (note or "").strip() or None
    payload = dict(channel=channel, thread_id=thread_id, outcome=outcome, reply=reply, note=note)
    posted = None
    with apply_change(session, actor) as cx:
        chat.parse_channel(session, channel)
        a = access.current(session)
        if a is not None and a.channel_level(channel) < access.COMMENT:
            from slate.service.errors import Forbidden

            raise Forbidden("comment access required")
        receipt = work._receipt(session, actor, TURN_EVENT, run_id, payload)
        if receipt is not None:
            return receipt["result"]
        row = _held(session, actor, channel, thread_id, now, run_id)
        if outcome == "done" and reply and reply.strip():
            # chat.post -> on_posted -> _answered ends the turn with this message as the reply.
            posted = chat.post(session, actor, channel, body_md=reply, parent_id=thread_id)
        view = chat.message_views(session, [posted])[0] if posted is not None else None
        # The receipt is the same event that settles this turn; it survives row removal.
        from fastapi.encoders import jsonable_encoder

        result = jsonable_encoder({"message": view, "pending": row.message_id > row.taken_id})
        _end(
            cx,
            session,
            row,
            "done" if outcome == "done" else "released",
            note=note,
            reply_id=posted.id if posted else None,
            run_digest=work._digest(run_id),
            request_digest=work._digest(payload),
            result=result,
        )
    return result


def expire(session: Session, now: datetime) -> int:
    """Scheduler hook: end every turn whose lease ran out (`state: expired`, actor `slate`)."""
    from slate.service.due import system_actor  # noqa: PLC0415

    rows = session.scalars(
        select(ChatWake).where(ChatWake.claimed_at.is_not(None), ChatWake.until < now)
    ).all()
    if not rows:
        return 0
    actor = system_actor(session)
    ended = 0
    for row in rows:
        with apply_change(session, actor) as cx:
            fresh = session.get(
                ChatWake, (row.account_id, row.channel, row.thread_id), populate_existing=True
            )
            if fresh is None or fresh.until is None or fresh.until >= now:
                continue
            row.until = None
            cx.record(
                TURN_EVENT,
                subject_id=row.message_id,
                after=_turn_after(
                    row,
                    _channel_name(session, row.channel),
                    "expired",
                    reconciliation_required=True,
                ),
                covers=[row],
            )
        ended += 1
    return ended


def reconcile(
    session: Session,
    actor: Actor,
    channel: str,
    thread_id: str | None = None,
    *,
    run_id: str,
    state: str,
    evidence: str,
) -> dict[str, Any]:
    """Unlock a quarantined chat only after the caller confirms remote terminal state."""
    if state not in ("stopped", "completed") or not evidence.strip():
        raise Invalid("terminal runtime state and evidence required")
    payload = dict(
        channel=channel, thread_id=thread_id, state=state, evidence=evidence, reconcile=True
    )
    with apply_change(session, actor) as cx:
        chat.parse_channel(session, channel)
        if work._receipt(session, actor, TURN_EVENT, run_id, payload):
            return {"reconciled": True}
        row = session.get(
            ChatWake, (actor.account_id, channel, _key(thread_id)), populate_existing=True
        )
        if row is None or not row.run_id or not secrets.compare_digest(row.run_id, run_id):
            raise Conflict("current run proof required for reconciliation")
        access.check_object(session, row)
        _end(
            cx,
            session,
            row,
            "released",
            reconciled=True,
            runtime_state=state,
            evidence=evidence,
            run_digest=work._digest(run_id),
            request_digest=work._digest(payload),
        )
    return {"reconciled": True}
