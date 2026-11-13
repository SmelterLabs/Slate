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
  `expire` (scheduler hook) ends a turn whose lease ran out (`state: expired`, actor `slate`)
  and keeps it quarantined until the agent reconciles, a person clears it (`clear`) or a
  person mentions the agent there again (a new turn).

The lease half (take, heartbeat, finish, expire, reconcile, clear, dropping a turn the agent
can no longer answer) is shared with doc turns in service/turns.py; this module keeps the
chat side: who is woken, what the runner is handed, how the answer is posted.

Agent machinery stays history: the wake, the take and the end are events; the row is the
lease. Rate limits are the token's as for any call. Slate knows nothing about what runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, ChatWake, Message, Role, utcnow
from slate.service import access, chat, turns
from slate.service.core import Actor
from slate.service.errors import Conflict, Forbidden, Invalid, NotFound

# Top-level messages handed to the agent as context for a channel-level mention (a thread hands
# over the whole thread).
TURN_MESSAGES = 20
OUTCOMES = turns.OUTCOMES
WAKE_EVENT = "message.mentioned_agent"
TURN_EVENT = "message.agent_turn"


_key = turns.key


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
        elif turns.fold(KIND, cx, row, ch.name, now, poster.handle):
            # A live turn is open there: the mention joins it; the next take answers it if the
            # run ends without having seen it.
            if row.taken_id is not None and row.since_id <= row.taken_id:
                row.since_id = msg.id
            row.message_id = msg.id
            queued.append(a.id)
        else:
            # Pending (or its run is gone, settled by fold): one turn answers the mentions.
            row.message_id = msg.id
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


def _require_comment(session: Session, channel: str) -> None:
    chat.parse_channel(session, channel)
    a = access.current(session)
    if a is not None and a.channel_level(channel) < access.COMMENT:
        raise Forbidden("comment access required")


def _not_archived(session: Session, row: ChatWake) -> None:
    if chat.parse_channel(session, row.channel).archived:
        raise Conflict("channel is archived; stop execution")


def _where(channel: str, thread_id: str | None) -> str:
    return f"in {channel}" + (f" thread {thread_id}" if thread_id else "")


KIND = turns.Kind(
    noun="chat",
    model=ChatWake,
    place="channel",
    turn_event=TURN_EVENT,
    latest="message_id",
    taken="taken_id",
    order=("since_id",),
    name=_channel_name,
    after=lambda row, name: {
        "channel": row.channel,
        "channel_name": name,
        "message_id": row.taken_id or row.message_id,
    },
    subject=lambda row: row.taken_id or row.message_id,
    view=turn_view,
    level=lambda session, a, channel: a.channel_level(channel),
    where=_where,
    require=_require_comment,
    reply_key="message",
    check_held=_not_archived,
)

lease_view = turns.lease_view


def remaining(session: Session, account_id: str) -> int:
    """How many chat turns the account could take now."""
    return turns.remaining(KIND, session, account_id)


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
    or None. With `run_id` and `resume`, the turn it already holds comes back with its lease
    renewed. A pending turn the agent can no longer answer is dropped, not an error."""
    return turns.take(
        KIND,
        session,
        actor,
        lease=lease,
        place=channel,
        thread_id=thread_id,
        resume=resume,
        run_id=run_id,
        now=now,
    )


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
    return turns.heartbeat(
        KIND, session, actor, channel, thread_id, lease=lease, now=now, run_id=run_id
    )


def _answered(cx: Any, session: Session, actor: Actor, msg: Message) -> None:
    row = session.get(ChatWake, (actor.account_id, msg.channel, _key(msg.parent_id)))
    if row is None or row.claimed_at is None or row.run_id is not None:
        return
    turns.end(KIND, cx, session, row, "done", reply_id=msg.id)


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

    def post(row: ChatWake, text: str) -> tuple[Message, dict[str, Any]]:
        posted = chat.post(session, actor, channel, body_md=text, parent_id=thread_id)
        return posted, chat.message_views(session, [posted])[0]

    return turns.finish(
        KIND,
        session,
        actor,
        channel,
        thread_id,
        outcome=outcome,
        run_id=run_id,
        reply=reply,
        note=note,
        now=now,
        post=post,
    )


def expire(session: Session, now: datetime) -> int:
    """Scheduler hook: end every turn whose lease ran out (`state: expired`, actor `slate`;
    quarantined until reconciled or cleared), and drop the turns the agent can no longer
    answer (`state: dropped`)."""
    return turns.expire(KIND, session, now)


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
    return turns.reconcile(
        KIND, session, actor, channel, thread_id, run_id=run_id, state=state, evidence=evidence
    )


def clear(
    session: Session,
    actor: Actor,
    channel: str,
    thread_id: str | None = None,
    *,
    agent_id: str,
) -> dict[str, Any]:
    """A person who may post in the channel confirms an agent's expired turn there is over:
    `{cleared, pending}` (service/turns.py `clear`)."""
    return turns.clear(KIND, session, actor, channel, thread_id, agent_id=agent_id)
