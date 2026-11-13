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
  `reconciliation_required`); `reconcile` unlocks it with run proof and runtime evidence, a
  person who may comment on the doc clears it (`clear`), and a person's new mention there
  settles it and starts a new turn.

The lease half is shared with chat turns in service/turns.py; this module keeps the doc side:
who is woken, what the runner is handed, how the answer is posted.

A doc Ask starts no task and no item run: the work is the answer on the page (AGENTS.md rule 7).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, Doc, DocComment, DocWake, utcnow
from slate.service import access, guests, turns
from slate.service.core import Actor, Change
from slate.service.errors import Forbidden

OUTCOMES = turns.OUTCOMES
WAKE_EVENT = "doc.mentioned_agent"
TURN_EVENT = "doc.agent_turn"
# The page text handed to the agent: the section around the mention, at most this long.
SECTION_MAX = 8000
_HEADING = re.compile(r"^(#{1,6})\s")


_key = turns.key


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
            # A live turn there: the mention joins it, and the next take answers it if the run
            # ends without having seen it. Pending, or its run is gone (settled by fold): one
            # turn answers the mentions.
            joins = turns.fold(KIND, cx, row, doc.title, now, poster.handle)
            for k, v in mention.items():
                setattr(row, k, v)
            row.mention_no = (row.mention_no or 0) + 1
            (queued if joins else woken).append(a.id)
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


def _title(session: Session, doc_id: str) -> str | None:
    return session.scalar(select(Doc.title).where(Doc.id == doc_id))


def _require_comment(session: Session, doc_id: str) -> None:
    from slate.service import docs as docs_svc  # noqa: PLC0415

    doc = docs_svc.get_doc(session, doc_id)
    a = access.current(session)
    if a is not None and a.doc_level(access.doc_place(session, doc)) < access.COMMENT:
        raise Forbidden("comment access required")


def _where(doc_id: str, thread_id: str | None) -> str:
    return f"on doc {doc_id}" + (f" thread {thread_id}" if thread_id else "")


KIND = turns.Kind(
    noun="doc",
    model=DocWake,
    place="doc_id",
    turn_event=TURN_EVENT,
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
    view=lambda session, row: turn_view(session, row),
    level=lambda session, a, doc_id: a.doc_level(access.doc_place(session, doc_id)),
    where=_where,
    require=_require_comment,
    reply_key="comment",
)

lease_view = turns.lease_view


def remaining(session: Session, account_id: str) -> int:
    """How many doc turns the account could take now."""
    return turns.remaining(KIND, session, account_id)


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
    or None. With `run_id` and `resume`, the turn it already holds comes back, lease renewed.
    A pending turn the agent can no longer answer is dropped, not an error."""
    return turns.take(
        KIND,
        session,
        actor,
        lease=lease,
        place=doc_id,
        thread_id=thread_id,
        resume=resume,
        run_id=run_id,
        now=now,
    )


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
    return turns.heartbeat(
        KIND, session, actor, doc_id, thread_id, lease=lease, now=now, run_id=run_id
    )


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
    from slate.service import doc_threads  # noqa: PLC0415

    def post(row: DocWake, text: str) -> tuple[DocComment, dict[str, Any]]:
        posted = doc_threads.add_comment(
            session, actor, doc_id, body_md=text, parent_id=row.thread_id or None
        )
        return posted, doc_threads.view(session, posted)

    return turns.finish(
        KIND,
        session,
        actor,
        doc_id,
        thread_id,
        outcome=outcome,
        run_id=run_id,
        reply=reply,
        note=note,
        now=now,
        post=post,
    )


def expire(session: Session, now: datetime) -> int:
    """Scheduler hook: end every turn whose lease ran out (`state: expired`, actor `slate`).
    The row stays claimed until a `reconcile` or a person's `clear`: the run may still be
    writing somewhere. Turns the agent can no longer answer are dropped (`state: dropped`)."""
    return turns.expire(KIND, session, now)


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
    return turns.reconcile(
        KIND, session, actor, doc_id, thread_id, run_id=run_id, state=state, evidence=evidence
    )


def clear(
    session: Session,
    actor: Actor,
    doc_id: str,
    thread_id: str | None = None,
    *,
    agent_id: str,
) -> dict[str, Any]:
    """A person who may comment on the doc confirms an agent's expired turn there is over:
    `{cleared, pending}` (service/turns.py `clear`)."""
    return turns.clear(KIND, session, actor, doc_id, thread_id, agent_id=agent_id)
