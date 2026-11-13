"""Agent turns: the lease half shared by chat turns (service/chatwake.py) and doc turns
(service/docwake.py).

A person's @mention of a push agent in a chat channel or on a page is a turn: one row per
(agent, place, thread), `ChatWake` for a channel and `DocWake` for a page, that the agent's
runner takes with a lease, keeps with heartbeats and ends with a finish. The two modules keep
what differs (who is woken, what the runner is handed, how the answer is posted); this module
holds the rest, parameterised by a `Kind`. The wire shapes are the two modules' own.

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

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, utcnow
from slate.service import access, work
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Conflict, Forbidden, Invalid

OUTCOMES = ("done", "release")


@dataclass(frozen=True)
class Kind:
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
    reply_key: str  # "message" | "comment": the posted reply in a finish result
    check_held: Callable[[Session, Any], None] = field(default=lambda session, row: None)


def key(thread_id: str | None) -> str:
    return thread_id or ""


def live(row: Any, now: datetime) -> bool:
    """Taken, and its lease has not run out."""
    return row.claimed_at is not None and row.until is not None and row.until >= now


def lease_view(row: Any) -> dict[str, Any]:
    return {
        "account_id": row.account_id,
        "claimed_at": row.claimed_at,
        "until": row.until,
        "run_id": row.run_id,
    }


def _name(k: Kind, session: Session, place: str) -> str | None:
    with access.unrestricted(session):
        return k.name(session, place)


def _own_access(session: Session, account_id: str) -> access.Access | None:
    """The agent's own access (its account, not the calling token's narrower limit)."""
    with access.unrestricted(session):
        account = session.get(Account, account_id)
        return access.compute(session, account) if account is not None else None


def _after(k: Kind, row: Any, name: str | None, state: str, **extra: Any) -> dict[str, Any]:
    return {
        **k.after(row, name),
        "thread_id": row.thread_id or None,
        "agent": row.account_id,
        "state": state,
        "pending": False,
        **extra,
    }


def _waiting(k: Kind, row: Any) -> bool:
    """A mention arrived after the open turn took its own."""
    taken = getattr(row, k.taken)
    return taken is not None and getattr(row, k.latest) > taken


def _reset(k: Kind, row: Any) -> None:
    row.claimed_at = None
    row.until = None
    row.run_id = None
    setattr(row, k.taken, None)


def _mine(k: Kind, account_id: str, place: str | None, thread_id: str | None) -> Any:
    q = select(k.model).where(k.model.account_id == account_id)
    if place:
        q = q.where(getattr(k.model, k.place) == place, k.model.thread_id == key(thread_id))
    return q


def _pending(k: Kind, q: Any) -> Any:
    return q.where(k.model.claimed_at.is_(None)).order_by(*(getattr(k.model, c) for c in k.order))


def remaining(k: Kind, session: Session, account_id: str) -> int:
    """How many turns of this kind the account could take now."""
    sub = (
        _pending(k, _mine(k, account_id, None, None))
        .order_by(None)
        .with_only_columns(k.model.account_id)
        .subquery()
    )
    return session.scalar(select(func.count()).select_from(sub)) or 0


# --- take, keep, end ----------------------------------------------------------------------


def take(
    k: Kind,
    session: Session,
    actor: Actor,
    *,
    lease: int | None,
    place: str | None,
    thread_id: str | None,
    resume: bool,
    run_id: str | None,
    now: datetime | None,
) -> dict[str, Any] | None:
    """Take the agent's next turn (at `place` and thread, when given): `{<noun>, resumed}`, or
    None. With `run_id` and `resume`, the turn it already holds comes back, lease renewed."""
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
    k: Kind, session: Session, own: access.Access | None, place: str, name: str | None
) -> str | None:
    """Why the agent can never answer a turn there, or None."""
    if name is None:
        return "gone"
    if own is None or k.level(session, own, place) < access.COMMENT:
        return "no_access"
    return None


def _drop(k: Kind, cx: Change, session: Session, row: Any, name: str | None, reason: str) -> None:
    """End a turn nobody can take (`state: dropped`): the board's "will answer" line clears."""
    after = _after(k, row, name, "dropped", reason=reason)
    subject = k.subject(row)
    session.delete(row)
    # The agent may no longer write there, which is why the turn goes: dropping it follows from
    # that (like `Change.derive`), so the caller's write check does not apply.
    with access.unrestricted(session):
        cx.record(k.turn_event, subject_id=subject, after=after, covers=[row])


def _held(
    k: Kind,
    session: Session,
    actor: Actor,
    place: str,
    thread_id: str | None,
    now: datetime,
    run_id: str | None = None,
) -> Any:
    row = session.get(k.model, (actor.account_id, place, key(thread_id)), populate_existing=True)
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
    k: Kind,
    session: Session,
    actor: Actor,
    place: str,
    thread_id: str | None,
    *,
    lease: int | None,
    now: datetime | None,
    run_id: str | None,
) -> Any:
    """Keep the turn: the lease runs `lease` seconds from now. No event (bookkeeping)."""
    seconds = work.lease_seconds(lease)
    now = now or utcnow()
    with apply_change(session, actor):
        row = _held(k, session, actor, place, thread_id, now, run_id)
        row.until = now + timedelta(seconds=seconds)
    return row


def end(k: Kind, cx: Change, session: Session, row: Any, state: str, **extra: Any) -> bool:
    """End the turn: the row goes, unless a mention arrived while it was open (then it is
    pending again, `after.pending` true, and the next take answers it). One event."""
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
    k: Kind,
    session: Session,
    actor: Actor,
    place: str,
    thread_id: str | None,
    *,
    outcome: str,
    run_id: str | None,
    reply: str | None,
    note: str | None,
    now: datetime | None,
    post: Callable[[Any, str], tuple[Any, dict[str, Any]]],
) -> dict[str, Any]:
    """End the turn with its proof: `done` with `reply` posts it (`post(row, reply)` returns
    the posted object and its view); `done` without one, or `release`, posts nothing. Returns
    `{<reply_key>, pending}`; an identical retry returns the same result (the receipt is the
    event that settled the turn, so it survives the row)."""
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
            posted, view = post(row, reply)
        result = jsonable_encoder({k.reply_key: view, "pending": _waiting(k, row)})
        end(
            k,
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
    k: Kind,
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
            k.model, (actor.account_id, place, key(thread_id)), populate_existing=True
        )
        if row is None or not row.run_id or not secrets.compare_digest(row.run_id, run_id):
            raise Conflict("current run proof required for reconciliation")
        access.check_object(session, row)
        end(
            k,
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
    k: Kind,
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
    now = now or utcnow()
    with apply_change(session, actor) as cx:
        k.require(session, place)
        me = session.get(Account, actor.account_id)
        if me is None or me.kind != AccountKind.person:
            raise Forbidden(
                "only a person clears an agent's expired turn; an agent reconciles its own "
                "with its run proof"
            )
        row = session.get(k.model, (agent_id, place, key(thread_id)), populate_existing=True)
        if row is None or row.claimed_at is None:
            raise Conflict(f"no expired turn {k.where(place, thread_id)} to clear")
        if live(row, now):
            raise Conflict(
                "the turn is live: it ends when the agent finishes or its lease runs out"
            )
        pending = end(
            k,
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


def fold(k: Kind, cx: Change, row: Any, name: str | None, now: datetime, by: str) -> bool:
    """A new mention on an existing turn row, inside the mention's change (no reads here: the
    caller has read everything first). True when it joins a live turn (`queued`: the next take
    answers it if that run ends without seeing it). Otherwise the row is made pending and the
    mention wakes the agent; a turn whose lease ran out is settled first (`state: released`,
    `reconciled`, by the mention's author), so a run that is gone never swallows it."""
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


def expire(k: Kind, session: Session, now: datetime) -> int:
    """Scheduler hook: end every turn whose lease ran out (`state: expired`, actor `slate`,
    `reconciliation_required`; the row stays claimed), then drop the turns nobody can take any
    more. Returns how many turns it ended or dropped."""
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


def _sweep(k: Kind, session: Session, now: datetime) -> int:
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
