"""A doc @mention starts a runner-backed agent (docs/agents-work-loop.md).

The chat design (service/chatwake.py) applied to pages: a doc Ask is a turn. A person who
@mentions an agent on a page, or in one of its review or discussion threads, expects an answer
on that page.

- `on_mentioned` (called where `doc.mentioned` is recorded: a page save that adds `@handle`s,
  docs._record_mentions; a doc comment that mentions or is addressed to someone,
  doc_threads._record_mentions): for each mentioned account that is an enabled agent with wake
  mode `push` and may comment on the doc (`access.compute` for the agent itself), one `DocWake`
  row per (agent, doc, thread) and one `doc.mentioned_agent` event (`after.for` = the agents
  woken, `after.queued` = those whose turn there is already open: the new mention folds into
  it). A mention by an agent never wakes another agent. `doc.mentioned` itself is unchanged,
  so the inbox and check-in still list the mention.
- `next_turn` (REST `POST /work/next {doc_id, thread_id}`, or a generic `/work/next` once no
  task and no chat turn is free): the runner takes the turn with a lease. `doc.agent_turn`
  `state: started`. The turn carries the page (id, title, path, the section of the body around
  the mention), the thread's comments, the mention and what the agent may do.
- `finish` (`POST /work/doc/finish`): `done` with `reply` posts it as the agent's comment in
  that thread, or as a new discussion thread on the page when the mention was in the body;
  `release` (or `done` without a reply) ends the turn with nothing posted. As in chat, a turn is
  fenced by its `run_id`: the agent's own comment does not end it, the finish does. A mention
  that arrived while the turn was open makes the row pending again (`after.pending` true).
- `heartbeat` moves the lease without an event (`until` is bookkeeping, core.BOOKKEEPING);
  `expire` (scheduler hook) ends a turn whose lease ran out (`state: expired`, actor `slate`,
  `reconciliation_required`); `reconcile` unlocks it with run proof and runtime evidence.

A doc Ask starts no task and no item run: the work is the answer on the page (AGENTS.md rule 7).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, Doc, DocComment, DocWake, utcnow
from slate.service import access, guests, work
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Conflict, Forbidden, Invalid

OUTCOMES = ("done", "release")
WAKE_EVENT = "doc.mentioned_agent"
TURN_EVENT = "doc.agent_turn"
# The page text handed to the agent: the section around the mention, at most this long.
SECTION_MAX = 8000
_HEADING = re.compile(r"^(#{1,6})\s")


def _key(thread_id: str | None) -> str:
    return thread_id or ""


def _level(session: Session, agent: Account, doc: Doc | str) -> int:
    """The agent's own access to the doc, not the poster's (whose filter would shape it)."""
    with access.unrestricted(session):
        a = access.compute(session, agent)
        return a.doc_level(access.doc_place(session, doc))


# --- the wake -------------------------------------------------------------------------------


def on_mentioned(
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
        if (a.wake or "push") == "push" and _level(session, a, doc) >= access.COMMENT
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
            for k, v in mention.items():
                setattr(row, k, v)
            row.mention_no = (row.mention_no or 0) + 1
            if row.claimed_at is not None:
                # A turn is open there: the mention joins it; the next take answers it if the
                # run ends without having seen it.
                queued.append(a.id)
            else:
                woken.append(a.id)
        covers.append(row)
    cx.record(
        WAKE_EVENT,
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


def lease_view(row: DocWake) -> dict[str, Any]:
    return {
        "account_id": row.account_id,
        "claimed_at": row.claimed_at,
        "until": row.until,
        "run_id": row.run_id,
    }


def turn_view(session: Session, row: DocWake) -> dict[str, Any]:
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
    level = _level(session, agent, doc) if agent else 0
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


def _mine(account_id: str, doc_id: str | None, thread_id: str | None) -> Any:
    q = select(DocWake).where(DocWake.account_id == account_id)
    if doc_id:
        q = q.where(DocWake.doc_id == doc_id, DocWake.thread_id == _key(thread_id))
    return q


def _pending(q: Any) -> Any:
    return q.where(DocWake.claimed_at.is_(None)).order_by(DocWake.created_at, DocWake.doc_id)


def remaining(session: Session, account_id: str) -> int:
    """How many doc turns the account could take now."""
    sub = (
        _pending(_mine(account_id, None, None))
        .order_by(None)
        .with_only_columns(DocWake.account_id)
        .subquery()
    )
    return session.scalar(select(func.count()).select_from(sub)) or 0


def next_turn(
    session: Session,
    actor: Actor,
    *,
    lease: int | None = None,
    doc_id: str | None = None,
    thread_id: str | None = None,
    resume: bool = True,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Take the agent's next doc turn (on that doc and thread, when given): `{doc, resumed}`,
    or None. With `run_id` and `resume`, the turn it already holds comes back, lease renewed."""
    if thread_id and not doc_id:
        raise Invalid("thread_id goes with doc_id")
    seconds = work.lease_seconds(lease)
    now = now or utcnow()
    with apply_change(session, actor) as cx:
        if run_id:
            if not resume or not doc_id:
                raise Invalid("run_id resume requires resume=true and doc_id")
            held = _held(session, actor, doc_id, thread_id, now, run_id)
            held.until = now + timedelta(seconds=seconds)
            return {"doc": turn_view(session, held), "resumed": True}
        base = _mine(actor.account_id, doc_id, thread_id)
        for row in session.scalars(_pending(base).execution_options(populate_existing=True)).all():
            doc = session.get(Doc, row.doc_id)
            if doc is None:
                continue  # out of the caller's sight now
            access.check_object(session, row)
            row.run_id = secrets.token_urlsafe(32)
            row.claimed_at = now
            row.until = now + timedelta(seconds=seconds)
            row.taken_no = row.mention_no
            cx.record(
                TURN_EVENT,
                subject_id=row.doc_id,
                after=_turn_after(row, doc.title, "started"),
                covers=[row],
            )
            return {"doc": turn_view(session, row), "resumed": False}
    return None


def _turn_after(row: DocWake, title: str | None, state: str, **extra: Any) -> dict[str, Any]:
    return {
        "doc_id": row.doc_id,
        "title": title,
        "thread_id": row.thread_id or None,
        "comment_id": row.comment_id,
        "agent": row.account_id,
        "state": state,
        "pending": False,
        **extra,
    }


def _title(session: Session, doc_id: str) -> str | None:
    with access.unrestricted(session):
        return session.scalar(select(Doc.title).where(Doc.id == doc_id))


def _held(
    session: Session,
    actor: Actor,
    doc_id: str,
    thread_id: str | None,
    now: datetime,
    run_id: str | None = None,
) -> DocWake:
    row = session.get(DocWake, (actor.account_id, doc_id, _key(thread_id)), populate_existing=True)
    if row is None or row.claimed_at is None or row.until is None or row.until < now:
        where = f"doc {doc_id}" + (f" thread {thread_id}" if thread_id else "")
        raise Conflict(f"you hold no open turn on {where}: take it with /work/next first")
    if not run_id or not row.run_id or not secrets.compare_digest(run_id, row.run_id):
        raise Conflict("current run_id proof required for doc turn")
    access.check_object(session, row)
    return row


def heartbeat(
    session: Session,
    actor: Actor,
    doc_id: str,
    thread_id: str | None = None,
    *,
    lease: int | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> DocWake:
    """Keep the turn: the lease runs `lease` seconds from now. No event (bookkeeping)."""
    seconds = work.lease_seconds(lease)
    now = now or utcnow()
    with apply_change(session, actor):
        row = _held(session, actor, doc_id, thread_id, now, run_id)
        row.until = now + timedelta(seconds=seconds)
    return row


def _end(cx: Change, session: Session, row: DocWake, state: str, **extra: Any) -> bool:
    """End the turn: the row goes, unless a mention arrived while it was open (then it is
    pending again, `after.pending` true, and the next take answers it). One event."""
    title = _title(session, row.doc_id)  # read before the row changes
    pending = row.taken_no is not None and row.mention_no > row.taken_no
    after = _turn_after(row, title, state, **extra)
    after["pending"] = pending
    if pending:
        row.claimed_at = None
        row.until = None
        row.taken_no = None
        row.run_id = None
    else:
        session.delete(row)
    cx.record(TURN_EVENT, subject_id=row.doc_id, after=after, covers=[row])
    return pending


def _require_comment(session: Session, doc_id: str) -> None:
    from slate.service import docs as docs_svc  # noqa: PLC0415

    doc = docs_svc.get_doc(session, doc_id)
    a = access.current(session)
    if a is not None and a.doc_level(access.doc_place(session, doc)) < access.COMMENT:
        raise Forbidden("comment access required")


def finish(
    session: Session,
    actor: Actor,
    doc_id: str,
    thread_id: str | None = None,
    *,
    outcome: str,
    run_id: str | None = None,
    reply: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """End the turn: `done` with `reply` posts it as the agent's comment in the thread (a new
    discussion thread on the page for a body mention); `done` without one, or `release`, ends
    it with nothing posted. Returns `{comment, pending}`."""
    from fastapi.encoders import jsonable_encoder  # noqa: PLC0415

    from slate.service import doc_threads  # noqa: PLC0415

    if outcome not in OUTCOMES:
        raise Invalid(f"outcome must be one of {', '.join(OUTCOMES)}")
    now = now or utcnow()
    note = (note or "").strip() or None
    payload = dict(doc_id=doc_id, thread_id=thread_id, outcome=outcome, reply=reply, note=note)
    posted = None
    with apply_change(session, actor) as cx:
        _require_comment(session, doc_id)
        receipt = work._receipt(session, actor, TURN_EVENT, run_id, payload)
        if receipt is not None:
            return receipt["result"]
        row = _held(session, actor, doc_id, thread_id, now, run_id)
        if outcome == "done" and reply and reply.strip():
            posted = doc_threads.add_comment(
                session, actor, doc_id, body_md=reply, parent_id=row.thread_id or None
            )
        view = doc_threads.view(session, posted) if posted is not None else None
        # The receipt is the same event that settles this turn; it survives row removal.
        result = jsonable_encoder({"comment": view, "pending": row.mention_no > row.taken_no})
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
    """Scheduler hook: end every turn whose lease ran out (`state: expired`, actor `slate`).
    The row stays claimed until a `reconcile`: the run may still be writing somewhere."""
    from slate.service.due import system_actor  # noqa: PLC0415

    rows = session.scalars(
        select(DocWake).where(DocWake.claimed_at.is_not(None), DocWake.until < now)
    ).all()
    if not rows:
        return 0
    actor = system_actor(session)
    ended = 0
    for row in rows:
        with apply_change(session, actor) as cx:
            fresh = session.get(
                DocWake, (row.account_id, row.doc_id, row.thread_id), populate_existing=True
            )
            if fresh is None or fresh.until is None or fresh.until >= now:
                continue
            fresh.until = None
            cx.record(
                TURN_EVENT,
                subject_id=fresh.doc_id,
                after=_turn_after(
                    fresh,
                    _title(session, fresh.doc_id),
                    "expired",
                    reconciliation_required=True,
                ),
                covers=[fresh],
            )
        ended += 1
    return ended


def reconcile(
    session: Session,
    actor: Actor,
    doc_id: str,
    thread_id: str | None = None,
    *,
    run_id: str,
    state: str,
    evidence: str,
) -> dict[str, Any]:
    """Unlock a quarantined doc turn only after the caller confirms remote terminal state."""
    if state not in ("stopped", "completed") or not evidence.strip():
        raise Invalid("terminal runtime state and evidence required")
    payload = dict(
        doc_id=doc_id, thread_id=thread_id, state=state, evidence=evidence, reconcile=True
    )
    with apply_change(session, actor) as cx:
        _require_comment(session, doc_id)
        if work._receipt(session, actor, TURN_EVENT, run_id, payload):
            return {"reconciled": True}
        row = session.get(
            DocWake, (actor.account_id, doc_id, _key(thread_id)), populate_existing=True
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
