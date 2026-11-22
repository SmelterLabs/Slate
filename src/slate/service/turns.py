"""Agent turns woken by chat and page mentions.

A person's @mention of a push agent in a chat channel or on a page is a turn: one row per
(agent, place, thread), `ChatWake` for a channel and `DocWake` for a page, that the agent's
runner takes with a lease, keeps with heartbeats and ends with a finish. The kind table
holds the wake model, mention lookup, runner context, and reply posting for each place.

- `take` hands out the agent's oldest pending turn, or the one asked for. A pending turn the
  agent itself can no longer answer (its share was lowered below comment or removed, the place
  is gone) is dropped (`state: dropped`, `reason`) instead of failing the call; one only the
  calling token cannot reach (a list-limited token) is skipped and left for another token.
- `heartbeat` moves `until` (bookkeeping, no event); `finish` and `reconcile` end the turn and
  need the run's proof (`run_id`).
- `expire` (scheduler hook) ends a turn whose lease ran out (`state: expired`,
  `reconciliation_required`). The run may still be writing somewhere, so the row stays claimed
  (quarantined) until the agent reconciles with its proof, a person clears it (`clear`), or a
  person mentions the agent there again (`fold`). The same sweep drops pending and quarantined
  turns the agent can no longer answer, so nothing waits forever for an agent shut out.
- `clear`: a person who may post (chat) or comment (doc) there confirms an expired turn is
  over: `state: released`, `reconciled`, the person as actor. A mention that waited in the
  turn is pending again, and the agent's next take answers it. A live turn is never cleared.
- `fold`: a new mention on an existing row joins a live turn (`queued`); on an expired turn it
  settles the dead run first (`state: released`, `reconciled`, the person as actor) and wakes
  the agent again, so a mention is never swallowed by a run that is gone.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.models import (
    Account,
    AccountKind,
    ChatWake,
    Doc,
    DocComment,
    DocWake,
    Message,
    utcnow,
)
from slate.service import access, chat, guests, work
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Conflict, Forbidden, Invalid, NotFound

OUTCOMES = ("done", "release")


@dataclass(frozen=True)
class _Kind:
    """What differs between chat turns and doc turns."""

    noun: str  # "chat" | "doc": the turn's key in a /work/next answer
    model: type  # ChatWake | DocWake
    place: str  # the row's place column: "channel" | "doc_id"
    turn_event: str
    latest: str  # the row's newest mention: "message_id" | "mention_no"
    taken: str  # the mention the open turn took: "taken_id" | "taken_no"
    order: tuple[str, ...]  # pending turns, oldest first
    name: Callable[[Session, str], str | None]  # the place's name, None when it is gone
    after: Callable[[Any, str | None], dict[str, Any]]  # the place fields of a turn event
    subject: Callable[[Any], str]  # a turn event's subject id
    view: Callable[[Session, Any], dict[str, Any]]  # what the runner is handed
    level: Callable[[Session, access.Access, str], int]  # an Access's level at the place
    where: Callable[[str, str | None], str]  # "in list:x thread y", for error messages
    require: Callable[[Session, str], None]  # the caller may comment there (404 / 403)
    post: Callable[[Session, Actor, Any, str, str | None, str], tuple[Any, dict[str, Any]]]
    reply_key: str  # "message" | "comment": the posted reply in a finish result
    check_held: Callable[[Session, Any], None] = field(default=lambda session, row: None)


def _key(thread_id: str | None) -> str:
    return thread_id or ""


# A turn row is `waiting` (mentioned, not taken), `working` (taken, lease live) or `expired`
# (taken, lease ran out: it waits for a reconcile, a person's clear or a new mention). The row
# is deleted when the turn ends, so there is no fourth state (`docs/state-machines.md`).
STATES = ("waiting", "working", "expired")


def state(row: Any, now: datetime) -> str:
    """The one rule for a turn row's state, for chat and doc turns alike."""
    if row.claimed_at is None:
        return "waiting"
    return "working" if row.until is not None and row.until >= now else "expired"


def live(row: Any, now: datetime) -> bool:
    """Taken, and its lease has not run out."""
    return state(row, now) == "working"


def lease_view(row: Any) -> dict[str, Any]:
    return {
        "account_id": row.account_id,
        "claimed_at": row.claimed_at,
        "until": row.until,
        "run_id": row.run_id,
    }


def _name(k: _Kind, session: Session, place: str) -> str | None:
    with access.unrestricted(session):
        return k.name(session, place)


def _own_access(session: Session, account_id: str) -> access.Access | None:
    """The agent's own access (its account, not the calling token's narrower limit)."""
    with access.unrestricted(session):
        account = session.get(Account, account_id)
        return access.compute(session, account) if account is not None else None


def _after(k: _Kind, row: Any, name: str | None, state: str, **extra: Any) -> dict[str, Any]:
    return {
        **k.after(row, name),
        "thread_id": row.thread_id or None,
        "agent": row.account_id,
        "state": state,
        "pending": False,
        **extra,
    }


def _waiting(k: _Kind, row: Any) -> bool:
    """A mention arrived after the open turn took its own."""
    taken = getattr(row, k.taken)
    return taken is not None and getattr(row, k.latest) > taken


def _reset(k: _Kind, row: Any) -> None:
    row.claimed_at = None
    row.until = None
    row.run_id = None
    setattr(row, k.taken, None)


def _mine(k: _Kind, account_id: str, place: str | None, thread_id: str | None) -> Any:
    q = select(k.model).where(k.model.account_id == account_id)
    if place:
        q = q.where(getattr(k.model, k.place) == place, k.model.thread_id == _key(thread_id))
    return q


def _pending(k: _Kind, q: Any) -> Any:
    return q.where(k.model.claimed_at.is_(None)).order_by(*(getattr(k.model, c) for c in k.order))


def remaining(kind: str, session: Session, account_id: str) -> int:
    """How many turns of this kind the account could take now."""
    k = _KINDS[kind]
    sub = (
        _pending(k, _mine(k, account_id, None, None))
        .order_by(None)
        .with_only_columns(k.model.account_id)
        .subquery()
    )
    return session.scalar(select(func.count()).select_from(sub)) or 0


# --- take, keep, end ----------------------------------------------------------------------


def take(
    kind: str,
    session: Session,
    actor: Actor,
    *,
    lease: int | None = None,
    place: str | None = None,
    thread_id: str | None = None,
    resume: bool = True,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Take the agent's next turn (at `place` and thread, when given): `{<noun>, resumed}`, or
    None. With `run_id` and `resume`, the turn it already holds comes back, lease renewed."""
    k = _KINDS[kind]
    if thread_id and not place:
        raise Invalid(f"thread_id goes with {k.place}")
    seconds = work.lease_seconds(lease)
    now = now or utcnow()
    with apply_change(session, actor) as cx:
        if run_id:
            if not resume or not place:
                raise Invalid(f"run_id resume requires resume=true and {k.place}")
            held = _held(k, session, actor, place, thread_id, now, run_id)
            held.until = now + timedelta(seconds=seconds)
            return {k.noun: k.view(session, held), "resumed": True}
        rows = session.scalars(
            _pending(k, _mine(k, actor.account_id, place, thread_id)).execution_options(
                populate_existing=True
            )
        ).all()
        if not rows:
            return None
        own = _own_access(session, actor.account_id)
        caller = access.current(session)
        for row in rows:
            at = getattr(row, k.place)
            name = _name(k, session, at)
            reason = _unanswerable(k, session, own, at, name)
            if reason is not None:
                _drop(k, cx, session, row, name, reason)
                continue
            if caller is not None and k.level(session, caller, at) < access.COMMENT:
                continue  # this token cannot answer there; another of the agent's may
            row.run_id = secrets.token_urlsafe(32)
            row.claimed_at = now
            row.until = now + timedelta(seconds=seconds)
            setattr(row, k.taken, getattr(row, k.latest))
            cx.record(
                k.turn_event,
                subject_id=k.subject(row),
                after=_after(k, row, name, "started"),
                covers=[row],
            )
            return {k.noun: k.view(session, row), "resumed": False}
    return None


def _unanswerable(
    k: _Kind, session: Session, own: access.Access | None, place: str, name: str | None
) -> str | None:
    """Why the agent can never answer a turn there, or None."""
    if name is None:
        return "gone"
    if own is None or k.level(session, own, place) < access.COMMENT:
        return "no_access"
    return None


def _drop(k: _Kind, cx: Change, session: Session, row: Any, name: str | None, reason: str) -> None:
    """End a turn nobody can take (`state: dropped`): the board's "will answer" line clears."""
    after = _after(k, row, name, "dropped", reason=reason)
    subject = k.subject(row)
    session.delete(row)
    # The agent may no longer write there, which is why the turn goes: dropping it follows from
    # that (like `Change.derive`), so the caller's write check does not apply.
    with access.unrestricted(session):
        cx.record(k.turn_event, subject_id=subject, after=after, covers=[row])


def _held(
    k: _Kind,
    session: Session,
    actor: Actor,
    place: str,
    thread_id: str | None,
    now: datetime,
    run_id: str | None = None,
) -> Any:
    row = session.get(k.model, (actor.account_id, place, _key(thread_id)), populate_existing=True)
    if row is None or not live(row, now):
        raise Conflict(
            f"you hold no open turn {k.where(place, thread_id)}: take it with /work/next first"
        )
    if not run_id or not row.run_id or not secrets.compare_digest(run_id, row.run_id):
        raise Conflict(f"current run_id proof required for {k.noun} turn")
    access.check_object(session, row)
    k.check_held(session, row)
    return row


def heartbeat(
    kind: str,
    session: Session,
    actor: Actor,
    place: str,
    thread_id: str | None,
    *,
    lease: int | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> Any:
    """Keep the turn: the lease runs `lease` seconds from now. No event (bookkeeping)."""
    k = _KINDS[kind]
    seconds = work.lease_seconds(lease)
    now = now or utcnow()
    with apply_change(session, actor):
        row = _held(k, session, actor, place, thread_id, now, run_id)
        row.until = now + timedelta(seconds=seconds)
    return row


def end(kind: str, cx: Change, session: Session, row: Any, state: str, **extra: Any) -> bool:
    """End the turn: the row goes, unless a mention arrived while it was open (then it is
    pending again, `after.pending` true, and the next take answers it). One event."""
    k = _KINDS[kind]
    name = _name(k, session, getattr(row, k.place))  # read before the row changes
    pending = _waiting(k, row)
    after = _after(k, row, name, state, **extra)
    after["pending"] = pending
    subject = k.subject(row)
    if pending:
        _reset(k, row)
    else:
        session.delete(row)
    cx.record(k.turn_event, subject_id=subject, after=after, covers=[row])
    return pending


def finish(
    kind: str,
    session: Session,
    actor: Actor,
    place: str,
    thread_id: str | None,
    *,
    outcome: str,
    run_id: str | None = None,
    reply: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """End the turn with its proof. `done` with `reply` posts it in the turn's place.
    `done` without a reply, or `release`, posts nothing. Returns
    `{<reply_key>, pending}`; an identical retry returns the same result (the receipt is the
    event that settled the turn, so it survives the row)."""
    k = _KINDS[kind]
    if outcome not in OUTCOMES:
        raise Invalid(f"outcome must be one of {', '.join(OUTCOMES)}")
    now = now or utcnow()
    note = (note or "").strip() or None
    payload = {
        k.place: place,
        "thread_id": thread_id,
        "outcome": outcome,
        "reply": reply,
        "note": note,
    }
    with apply_change(session, actor) as cx:
        k.require(session, place)
        receipt = work._receipt(session, actor, k.turn_event, run_id, payload)
        if receipt is not None:
            return receipt["result"]
        row = _held(k, session, actor, place, thread_id, now, run_id)
        posted, view = None, None
        if outcome == "done" and reply and reply.strip():
            posted, view = k.post(session, actor, row, place, thread_id, reply)
        result = jsonable_encoder({k.reply_key: view, "pending": _waiting(k, row)})
        end(
            kind,
            cx,
            session,
            row,
            "done" if outcome == "done" else "released",
            note=note,
            reply_id=posted.id if posted is not None else None,
            run_digest=work._digest(run_id),
            request_digest=work._digest(payload),
            result=result,
        )
    return result


def reconcile(
    kind: str,
    session: Session,
    actor: Actor,
    place: str,
    thread_id: str | None,
    *,
    run_id: str,
    state: str,
    evidence: str,
) -> dict[str, Any]:
    """Unlock a quarantined turn only after its agent confirms the remote run's terminal state
    with the run's proof."""
    k = _KINDS[kind]
    if state not in ("stopped", "completed") or not evidence.strip():
        raise Invalid("terminal runtime state and evidence required")
    payload = {
        k.place: place,
        "thread_id": thread_id,
        "state": state,
        "evidence": evidence,
        "reconcile": True,
    }
    with apply_change(session, actor) as cx:
        k.require(session, place)
        if work._receipt(session, actor, k.turn_event, run_id, payload):
            return {"reconciled": True}
        row = session.get(
            k.model, (actor.account_id, place, _key(thread_id)), populate_existing=True
        )
        if row is None or not row.run_id or not secrets.compare_digest(row.run_id, run_id):
            raise Conflict("current run proof required for reconciliation")
        access.check_object(session, row)
        end(
            kind,
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


def clear(
    kind: str,
    session: Session,
    actor: Actor,
    place: str,
    thread_id: str | None,
    *,
    agent_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A person's confirmation that an agent's expired turn there is over (the runner lost its
    proof, crashed or was replaced). Only a person who may comment there, only an expired turn:
    a live one ends when the agent finishes or its lease runs out, and an agent settles its own
    with `reconcile`. Returns `{cleared, pending}`; `pending` true when a mention waited in the
    turn and the agent's next take answers it."""
    k = _KINDS[kind]
    now = now or utcnow()
    with apply_change(session, actor) as cx:
        k.require(session, place)
        me = session.get(Account, actor.account_id)
        if me is None or me.kind != AccountKind.person:
            raise Forbidden(
                "only a person clears an agent's expired turn; an agent reconciles its own "
                "with its run proof"
            )
        row = session.get(k.model, (agent_id, place, _key(thread_id)), populate_existing=True)
        if row is None or row.claimed_at is None:
            raise Conflict(f"no expired turn {k.where(place, thread_id)} to clear")
        if live(row, now):
            raise Conflict(
                "the turn is live: it ends when the agent finishes or its lease runs out"
            )
        pending = end(
            kind,
            cx,
            session,
            row,
            "released",
            reconciled=True,
            runtime_state="stopped",
            evidence=f"{me.handle} cleared the expired turn",
            run_digest=work._digest(row.run_id) if row.run_id else None,
        )
    return {"cleared": True, "pending": pending}


def fold(kind: str, cx: Change, row: Any, name: str | None, now: datetime, by: str) -> bool:
    """A new mention on an existing turn row, inside the mention's change (no reads here: the
    caller has read everything first). True when it joins a live turn (`queued`: the next take
    answers it if that run ends without seeing it). Otherwise the row is made pending and the
    mention wakes the agent; a turn whose lease ran out is settled first (`state: released`,
    `reconciled`, by the mention's author), so a run that is gone never swallows it."""
    k = _KINDS[kind]
    if row.claimed_at is None:
        return False
    if live(row, now):
        return True
    after = _after(
        k,
        row,
        name,
        "released",
        reconciled=True,
        evidence=f"{by} mentioned the agent again after the turn's lease ran out",
        run_digest=work._digest(row.run_id) if row.run_id else None,
    )
    after["pending"] = True
    cx.record(k.turn_event, subject_id=k.subject(row), after=after, covers=[row])
    _reset(k, row)
    return False


# --- the scheduler -------------------------------------------------------------------------


def expire(kind: str, session: Session, now: datetime) -> int:
    """Scheduler hook: end every turn whose lease ran out (`state: expired`, actor `slate`,
    `reconciliation_required`; the row stays claimed), then drop the turns nobody can take any
    more. Returns how many turns it ended or dropped."""
    k = _KINDS[kind]
    from slate.service.due import system_actor  # noqa: PLC0415

    rows = session.scalars(
        select(k.model).where(k.model.claimed_at.is_not(None), k.model.until < now)
    ).all()
    ended = 0
    actor = system_actor(session) if rows else None
    for row in rows:
        with apply_change(session, actor) as cx:
            pk = (row.account_id, getattr(row, k.place), row.thread_id)
            fresh = session.get(k.model, pk, populate_existing=True)
            if fresh is None or fresh.until is None or fresh.until >= now:
                continue
            name = _name(k, session, pk[1])
            fresh.until = None
            cx.record(
                k.turn_event,
                subject_id=k.subject(fresh),
                after=_after(k, fresh, name, "expired", reconciliation_required=True),
                covers=[fresh],
            )
        ended += 1
    return ended + _sweep(k, session, now)


def _sweep(k: _Kind, session: Session, now: datetime) -> int:
    """Drop the pending and expired turns whose agent can no longer answer there (its share was
    lowered or removed, the place is gone). Unseen by the agent, they would wait forever."""
    from slate.service.due import system_actor  # noqa: PLC0415

    rows = session.scalars(
        select(k.model).where(or_(k.model.claimed_at.is_(None), k.model.until.is_(None)))
    ).all()
    seen: dict[str, access.Access | None] = {}
    dropped = 0
    actor = system_actor(session) if rows else None
    for row in rows:
        if row.account_id not in seen:
            seen[row.account_id] = _own_access(session, row.account_id)
        pk = (row.account_id, getattr(row, k.place), row.thread_id)
        name = _name(k, session, pk[1])
        reason = _unanswerable(k, session, seen[row.account_id], pk[1], name)
        if reason is None:
            continue
        with apply_change(session, actor) as cx:
            fresh = session.get(k.model, pk, populate_existing=True)
            if fresh is None or live(fresh, now):
                continue
            _drop(k, cx, session, fresh, name, reason)
        dropped += 1
    return dropped


# Chat mentions and replies

TURN_MESSAGES = 20


def _channel_name(session: Session, key: str) -> str | None:
    try:
        return chat.parse_channel(session, key).name
    except (NotFound, Invalid):
        return None


def _chat_view(session: Session, row: ChatWake) -> dict[str, Any]:
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


def _require_chat_comment(session: Session, channel: str) -> None:
    chat.parse_channel(session, channel)
    a = access.current(session)
    if a is not None and a.channel_level(channel) < access.COMMENT:
        raise Forbidden("comment access required")


def _not_archived(session: Session, row: ChatWake) -> None:
    if chat.parse_channel(session, row.channel).archived:
        raise Conflict("channel is archived; stop execution")


def _chat_where(channel: str, thread_id: str | None) -> str:
    return f"in {channel}" + (f" thread {thread_id}" if thread_id else "")


def _post_chat(
    session: Session, actor: Actor, row: ChatWake, channel: str, thread_id: str | None, text: str
) -> tuple[Message, dict[str, Any]]:
    posted = chat.post(session, actor, channel, body_md=text, parent_id=thread_id)
    return posted, chat.message_views(session, [posted])[0]


# Page mentions and replies

SECTION_MAX = 8000
_HEADING = re.compile(r"^(#{1,6})\s")


def _doc_level(session: Session, agent: Account, doc: Doc | str) -> int:
    """The agent's own access to the doc, not the poster's (whose filter would shape it)."""
    with access.unrestricted(session):
        a = access.compute(session, agent)
        return a.doc_level(access.doc_place(session, doc))


# --- the wake -------------------------------------------------------------------------------


def on_doc_mentioned(
    cx: Change,
    session: Session,
    doc: Doc,
    handles: list[str],
    *,
    comment: DocComment | None = None,
    excerpt: str = "",
) -> None:
    """Wake every push agent among `handles` that may comment on `doc`, unless the poster is an
    agent. `comment` is the mentioning comment (None for a mention in the page body). One
    `doc.mentioned_agent` event when anyone is woken or queued. A guest never wakes a workspace
    agent (service/guests.py)."""
    handles = guests.reachable_handles(session, cx.actor.account_id, handles)
    if not handles:
        return
    poster = session.get(Account, cx.actor.account_id)
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
        if (a.wake or "push") == "push" and _doc_level(session, a, doc) >= access.COMMENT
    ]
    if not agents:
        return
    agents.sort(key=lambda a: a.id)
    thread_key = (comment.parent_id or comment.id) if comment is not None else ""
    now = utcnow()
    # Every read first: a query after the first row changes would flush it uncovered.
    rows = {
        r.account_id: r
        for r in session.scalars(
            select(DocWake).where(
                DocWake.account_id.in_([a.id for a in agents]),
                DocWake.doc_id == doc.id,
                DocWake.thread_id == thread_key,
            )
        )
    }
    woken: list[str] = []
    queued: list[str] = []
    covers: list[DocWake] = []
    mention = dict(
        comment_id=comment.id if comment is not None else None,
        by_id=poster.id,
        excerpt=excerpt[:200],
    )
    for a in agents:
        row = rows.get(a.id)
        if row is None:
            row = DocWake(
                account_id=a.id,
                doc_id=doc.id,
                thread_id=thread_key,
                mention_no=1,
                created_at=now,
                **mention,
            )
            session.add(row)
            woken.append(a.id)
        else:
            # A live turn there: the mention joins it, and the next take answers it if the run
            # ends without having seen it. Pending, or its run is gone (settled by fold): one
            # turn answers the mentions.
            joins = fold("doc", cx, row, doc.title, now, poster.handle)
            for k, v in mention.items():
                setattr(row, k, v)
            row.mention_no = (row.mention_no or 0) + 1
            (queued if joins else woken).append(a.id)
        covers.append(row)
    cx.record(
        "doc.mentioned_agent",
        subject_id=doc.id,
        after={
            "doc_id": doc.id,
            "title": doc.title,
            "thread_id": thread_key or None,
            "comment_id": mention["comment_id"],
            "excerpt": mention["excerpt"],
            "for": woken,
            "queued": queued,
        },
        covers=covers,
    )


# --- the turn -------------------------------------------------------------------------------


def section_around(body: str, needle: str | None) -> str:
    """The heading section of `body` that holds `needle` (case-insensitive), else the start of
    the body; at most SECTION_MAX characters, centred on the needle when it is longer."""
    lines = (body or "").splitlines()
    if not lines:
        return ""
    low = needle.lower() if needle else None
    at = next((i for i, ln in enumerate(lines) if low and low in ln.lower()), None)
    if at is None:
        return "\n".join(lines)[:SECTION_MAX]
    start, level = 0, 7
    for i in range(at, -1, -1):
        m = _HEADING.match(lines[i])
        if m:
            start, level = i, len(m.group(1))
            break
    end = len(lines)
    for i in range(start + 1, len(lines)):
        m = _HEADING.match(lines[i])
        if m and len(m.group(1)) <= level:
            end = i
            break
    text = "\n".join(lines[start:end])
    if len(text) <= SECTION_MAX:
        return text
    offset = sum(len(ln) + 1 for ln in lines[start:at])
    lo = max(0, min(offset - SECTION_MAX // 2, len(text) - SECTION_MAX))
    return text[lo : lo + SECTION_MAX]


def _path(session: Session, doc: Doc) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = {doc.id}
    cur = session.get(Doc, doc.parent_id) if doc.parent_id else None
    while cur is not None and cur.id not in seen:
        out.append({"id": cur.id, "title": cur.title})
        seen.add(cur.id)
        cur = session.get(Doc, cur.parent_id) if cur.parent_id else None
    return list(reversed(out))


def _doc_view(session: Session, row: DocWake) -> dict[str, Any]:
    """What the runner hands the agent: the page, where on it, which mention, the thread's
    comments (oldest first) and what the agent may do there."""
    from slate.service import doc_threads  # noqa: PLC0415 (doc_threads calls this module)

    doc = session.get(Doc, row.doc_id)
    agent = session.get(Account, row.account_id)
    by = session.get(Account, row.by_id)
    thread: list[DocComment] = []
    if row.thread_id:
        thread = list(
            session.scalars(
                select(DocComment)
                .where(
                    DocComment.doc_id == doc.id,
                    or_(DocComment.id == row.thread_id, DocComment.parent_id == row.thread_id),
                )
                .order_by(DocComment.created_at, DocComment.id)
            )
        )
    comments = doc_threads.views(session, doc, thread) if thread else []
    root = thread[0] if thread and thread[0].id == row.thread_id else None
    if doc.kind == "file":
        section = ""
    elif root is not None and root.anchor_quote:
        section = section_around(doc.body_md, root.anchor_quote)
    else:
        section = section_around(doc.body_md, f"@{agent.handle}" if agent else None)
    level = _doc_level(session, agent, doc) if agent else 0
    return {
        "doc_id": doc.id,
        "title": doc.title,
        "kind": doc.kind,
        "path": _path(session, doc),
        "thread_id": row.thread_id or None,
        "comment_id": row.comment_id,
        "mention": {
            "excerpt": row.excerpt,
            "by_id": row.by_id,
            "by_handle": by.handle if by else None,
            "by_kind": by.kind.value if by else None,
            "comment": next((c for c in comments if c["id"] == row.comment_id), None),
        },
        "section_md": section,
        "body_chars": len(doc.body_md or ""),
        "comments": comments,
        "may": {"reply": True, "edit": doc.kind == "page" and level >= access.EDIT},
        "lease": lease_view(row),
    }


def _title(session: Session, doc_id: str) -> str | None:
    return session.scalar(select(Doc.title).where(Doc.id == doc_id))


def _require_doc_comment(session: Session, doc_id: str) -> None:
    from slate.service import docs as docs_svc  # noqa: PLC0415

    doc = docs_svc.get_doc(session, doc_id)
    a = access.current(session)
    if a is not None and a.doc_level(access.doc_place(session, doc)) < access.COMMENT:
        raise Forbidden("comment access required")


def _doc_where(doc_id: str, thread_id: str | None) -> str:
    return f"on doc {doc_id}" + (f" thread {thread_id}" if thread_id else "")


def _post_doc(
    session: Session, actor: Actor, row: DocWake, doc_id: str, thread_id: str | None, text: str
) -> tuple[DocComment, dict[str, Any]]:
    from slate.service import doc_threads  # noqa: PLC0415

    posted = doc_threads.add_comment(
        session, actor, doc_id, body_md=text, parent_id=row.thread_id or None
    )
    return posted, doc_threads.view(session, posted)


_KINDS = {
    "chat": _Kind(
        noun="chat",
        model=ChatWake,
        place="channel",
        turn_event="message.agent_turn",
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
        view=_chat_view,
        level=lambda session, a, channel: a.channel_level(channel),
        where=_chat_where,
        require=_require_chat_comment,
        post=_post_chat,
        reply_key="message",
        check_held=_not_archived,
    ),
    "doc": _Kind(
        noun="doc",
        model=DocWake,
        place="doc_id",
        turn_event="doc.agent_turn",
        latest="mention_no",
        taken="taken_no",
        order=("created_at", "doc_id"),
        name=_title,
        after=lambda row, title: {
            "doc_id": row.doc_id,
            "title": title,
            "comment_id": row.comment_id,
        },
        subject=lambda row: row.doc_id,
        view=_doc_view,
        level=lambda session, a, doc_id: a.doc_level(access.doc_place(session, doc_id)),
        where=_doc_where,
        require=_require_doc_comment,
        post=_post_doc,
        reply_key="comment",
    ),
}
