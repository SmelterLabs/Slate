"""Decide which chat mentions wake an agent and record those wakes with the message."""

from __future__ import annotations

from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, ChatWake, Message, Role, utcnow
from slate.service import access, chat, guests, turns
from slate.service.core import Actor

# The board's end-to-end harness advances chat leases through this binding.
expire = partial(turns.expire, "chat")


def _chat_can_post(session: Session, agent: Account, channel: str) -> bool:
    """May this agent read and post in the channel? Computed for the agent itself, not for
    whoever is posting now (whose filter would otherwise shape the answer)."""
    with access.unrestricted(session):
        a = access.compute(session, agent)
    return a.channel_level(channel) >= access.COMMENT


def _chat_reaches(poster: Account, agent: Account, msg: Message) -> bool:
    """A project's guests talk with the project's own agent, never a workspace agent (a guest's
    `@my-agent` wakes nothing); a members-only message never wakes a guest agent, which could not
    read it."""
    if poster.role == Role.guest and guests.workspace_agent(agent):
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
        and _chat_can_post(session, a, ch.key)
        and _chat_reaches(poster, a, msg)
    ]
    if not agents:
        return
    agents.sort(key=lambda a: a.id)
    thread_key = msg.parent_id or ""
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
        elif turns.fold("chat", cx, row, ch.name, now, poster.handle):
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
        "message.mentioned_agent",
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


def _answered(cx: Any, session: Session, actor: Actor, msg: Message) -> None:
    row = session.get(ChatWake, (actor.account_id, msg.channel, msg.parent_id or ""))
    if row is None or row.claimed_at is None or row.run_id is not None:
        return
    turns.end("chat", cx, session, row, "done", reply_id=msg.id)
