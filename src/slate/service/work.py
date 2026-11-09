"""The agent work loop: take the next task, keep it while working, report back in one call.

    next_task(session, actor, account, lease_seconds=900)   # REST POST /work/next, MCP slate_work
    heartbeat(session, actor, item_id)                       # keep the lease alive
    finish(session, actor, item_id, outcome="done", note="Sent")

A take is an ordinary claim (`reports.report(state="working", claim=True)`): one
`item.reported` event, the card's live badge, the agent added to the assignees. On top of it
the agent holds a `WorkLease` (models.WorkLease): until `until` nobody else can take the task;
`heartbeat` moves `until` without an event (bookkeeping, like a token's `last_used_at`); the
scheduler hook `expire_leases` gives an abandoned task back with an `item.reported` idle event by
`system` (and un-assigns the agent when the take had assigned it from the pool).

Which task: `eligible()` in SQL, best first.

- Scope: open tasks assigned to the account or waiting on it; with `pool`, also tasks with no
  assignee in lists the caller may edit. Narrowed by `list_id`, `section_id`, `tag`.
- Skipped: done/cancelled, archived (or in an archived list or section), blocked by an open
  task (`links.blocked_item_ids()`, the same rule as the item view's `blocked`), not started yet
  (`start_at` in the future), due-gated (below), waiting on someone else, flagged `review`
  (waiting on a person), reported `blocked` by you in the last `BLOCKED_PAUSE` (unless someone
  has since asked you something on it: a reply, below), and held by another account (a live
  lease, or with no lease a badge younger than `reports.CLAIM_FRESH`; a parked `blocked` or
  `review` badge with no lease does not hold the card against an Ask for you). A Run now
  pressed after the last report overrides waiting on someone, `review` and the blocked pause:
  whoever pressed it wants the agent to go on. A pending Ask for the account (not consumed by a
  work finish) overrides the same and a done status too: a question is answered now, whatever
  the card says.

Reply turns: when a task is `blocked` by an agent and waiting on a person, that person's next
comment on it is a reply to the agent (comments.add_comment): it is addressed to the agent as
an Ask, and the task stops waiting on them. The Ask is a wake (`comment.assigned` in
`WAKE_TYPES`), puts the task back in the agent's scope and ends its blocked pause, and
`/work/next` hands the comment back as `reply`.
- Order: your own before the pool; then priority (urgent first), soonest due (no due date
  last), board position, oldest.

Due gate: when the caller is an agent, a task assigned to it that has a due date waits for its
run time (`dates.run_at`: 06:00 workspace time on the day for a date-only due date, else the
due time), unless someone pressed Run now since its last take
(`items.run_requested_at`, set by `request_run`, cleared by `take`). Tasks with no due date, and
tasks that are the caller's only through `waiting_on` or the pool, are not gated.

Run now: `request_run` records `item.run_requested` (`after.for` = the item's agent assignees),
which the agent stream turns into a `work` hint. The scheduler records the same event at the run
time of a due-gated task (service/due.py, actor `slate`, `after.reason` = `due`), so the timer and
the button reach an agent's runner by one path. `next_task(item_id=...)` takes that one task or
nothing.

With `resume` (the default) a task the account already holds comes back first, its lease
renewed: an agent that crashed and restarted picks up where it left off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, case, false, func, not_, or_, select, true
from sqlalchemy.orm import Session, aliased

from slate import dates
from slate.models import (
    Account,
    AccountKind,
    AgentState,
    Comment,
    Event,
    Item,
    ItemAssignee,
    ItemKind,
    ItemTag,
    List,
    Role,
    Section,
    Status,
    StatusKind,
    Tag,
    WorkLease,
    utcnow,
)
from slate.service import access, comments, guests, links, reports
from slate.service.core import Actor, apply_change
from slate.service.errors import Conflict, Invalid
from slate.service.items import PRIORITY_RANK, get_item, resolve_account, update_item

log = logging.getLogger(__name__)

DEFAULT_LEASE = 15 * 60
MIN_LEASE = 60
MAX_LEASE = 24 * 60 * 60
TRIES = 5  # candidates tried per call when other agents win the race for them
OUTCOMES = ("done", "review", "blocked", "release")
CONTEXT_ENTRIES = 10
# A task you reported blocked stays out of your `next` this long (unless the badge is cleared),
# so a failing task is not retried at once; one waiting on someone else stays out until then.
BLOCKED_PAUSE = timedelta(hours=1)


def lease_seconds(value: int | None) -> int:
    seconds = DEFAULT_LEASE if value is None else int(value)
    if not MIN_LEASE <= seconds <= MAX_LEASE:
        raise Invalid(f"lease_seconds is {MIN_LEASE} to {MAX_LEASE}")
    return seconds


# --- Which task ------------------------------------------------------------------------------


def _scope_lists(session: Session) -> list[str] | None:
    """None when the caller may edit every list, else the ids of the lists it may edit."""
    a = access.current(session)
    if a is None or a.lists is None:
        return None if a is None or a.cap >= access.EDIT else []
    return [lid for lid, level in a.lists.items() if level >= access.EDIT]


def eligible(
    session: Session,
    account_id: str,
    *,
    now: datetime | None = None,
    pool: bool = False,
    list_id: str | None = None,
    section_id: str | None = None,
    tag: str | None = None,
    held: bool = False,
    item_id: str | None = None,
) -> Any:
    """A select of items the account could take now, best first (see the module docstring).

    `held=True` selects instead the open tasks the account already holds (its live lease, or
    its own `working` badge)."""
    now = now or utcnow()
    lease = aliased(WorkLease)
    mine = (
        select(ItemAssignee.item_id.label("id"))
        .where(ItemAssignee.account_id == account_id)
        .union(
            select(Item.id).where(Item.waiting_on_account_id == account_id),
            _asked_items(account_id),
        )
        .subquery()
    )
    is_mine = Item.id.in_(select(mine.c.id))
    # An open Ask for this account (a question on the card): it is answered now, even on a
    # card that is ready for review, waiting on someone or already done (card chat).
    asked = Item.id.in_(_asked_items(account_id))
    q = (
        select(Item)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .outerjoin(lease, lease.item_id == Item.id)
        .where(
            Item.kind == ItemKind.task,
            Item.archived_at.is_(None),
            List.archived_at.is_(None),
            Section.archived_at.is_(None),
        )
    )
    open_status = or_(
        Status.kind.is_(None), Status.kind.not_in([StatusKind.done, StatusKind.cancelled])
    )
    # `held` lists only open work; a finished card is taken again only to answer an Ask.
    q = q.where(open_status if held else or_(open_status, asked))
    editable = _scope_lists(session)
    if editable is not None:
        q = q.where(Item.list_id.in_(editable))
    if item_id:
        q = q.where(Item.id == item_id)
    if list_id:
        q = q.where(Item.list_id == list_id)
    if section_id:
        q = q.where(List.section_id == section_id)
    if tag:
        q = q.where(
            Item.id.in_(
                select(ItemTag.item_id)
                .join(Tag, Tag.id == ItemTag.tag_id)
                .where(Tag.name == tag.strip().lower())
            )
        )
    # Every branch tests for NULL first: NOT over a NULL comparison would drop the row.
    i_hold = or_(
        and_(lease.item_id.is_not(None), lease.account_id == account_id),
        and_(
            lease.item_id.is_(None),
            Item.agent_state.is_not(None),
            Item.agent_state == AgentState.working,
            Item.agent_state_by == account_id,
        ),
    )
    if held:
        return q.where(i_hold).order_by(Item.agent_state_at.desc(), Item.id)

    unassigned = Item.id.not_in(select(ItemAssignee.item_id))
    held_by_other = and_(
        Item.agent_state.is_not(None),
        Item.agent_state_by.is_not(None),
        Item.agent_state_by != account_id,
        or_(
            and_(lease.item_id.is_not(None), lease.until >= now),
            and_(
                lease.item_id.is_(None),
                Item.agent_state_at.is_not(None),
                Item.agent_state_at > now - reports.CLAIM_FRESH,
                # Another agent's parked badge (blocked or review, no lease) is not work in
                # progress: an Ask for this account goes through it (reports._check_claim).
                or_(Item.agent_state == AgentState.working, not_(asked)),
            ),
        ),
    )
    # Run now pressed since the last report: it overrides waiting on someone, the review badge
    # and the blocked pause (the person who pressed it wants the agent to go on).
    pressed = and_(
        Item.run_requested_at.is_not(None),
        or_(Item.agent_state_at.is_(None), Item.run_requested_at > Item.agent_state_at),
    )
    q = q.where(
        lease.item_id.is_(None),  # expired is unknown, not proof of remote cancellation
        or_(is_mine, and_(unassigned, true() if pool else false())),
        Item.id.not_in(links.blocked_item_ids()),
        or_(Item.start_at.is_(None), Item.start_at <= now),
        or_(
            Item.waiting_on_account_id.is_(None),
            Item.waiting_on_account_id == account_id,
            pressed,
            asked,
        ),
        or_(Item.agent_state.is_(None), Item.agent_state != AgentState.review, pressed, asked),
        pressed
        | not_(
            and_(
                Item.agent_state.is_not(None),
                Item.agent_state == AgentState.blocked,
                Item.agent_state_by == account_id,
                Item.agent_state_at.is_not(None),
                Item.agent_state_at > now - BLOCKED_PAUSE,
                # An open Ask for you (a reply to your question) ends the pause at once.
                Item.id.not_in(_asked_items(account_id)),
            )
        ),
        not_(held_by_other),
        not_(i_hold),
    )
    if _is_agent(session, account_id):
        q = q.where(_run_time_has_come(account_id, now))
    priority_rank = case(
        *((Item.priority == p, r) for p, r in PRIORITY_RANK.items()), else_=len(PRIORITY_RANK)
    )
    return q.order_by(
        case((is_mine, 0), else_=1),
        priority_rank,
        Item.due_at.is_(None),
        Item.due_at,
        Item.position,
        Item.created_at,
        Item.id,
    )


def _is_agent(session: Session, account_id: str) -> bool:
    kind = session.scalar(select(Account.kind).where(Account.id == account_id))
    return kind == AccountKind.agent


def _run_time_has_come(account_id: str, now: datetime) -> Any:
    """The due gate as a SQL condition (module docstring): true for tasks the agent may take.
    An open Ask (assigned comment) for the agent opens it: a question is answered now."""
    assigned = Item.id.in_(
        select(ItemAssignee.item_id).where(ItemAssignee.account_id == account_id)
    )
    early = dates.early_date_only_due(now)
    return or_(
        not_(assigned),
        Item.id.in_(_asked_items(account_id)),
        Item.due_at.is_(None),
        Item.due_at <= now,
        and_(Item.due_date_only, Item.due_at == early) if early is not None else false(),
        Item.run_requested_at.is_not(None),
    )


def _asked_items(account_id: str) -> Any:
    return _pending_asks(account_id).with_only_columns(Comment.item_id)


def _assignment_id() -> Any:
    return (
        select(func.max(Event.id))
        .where(Event.type == "comment.assigned", Event.subject_id == Comment.id)
        .correlate(Comment)
        .scalar_subquery()
    )


def _pending_asks(account_id: str) -> Any:
    # Event ids are ordered cursors. A finish consumes only assignments preceding its
    # take, not newer/reassigned Asks. Visibility in Today/inbox is deliberately unchanged.
    settled = (
        select(func.max(Event.after_json["through"].as_string()))
        .where(
            Event.type == "item.work_finished",
            Event.item_id == Comment.item_id,
            Event.actor_id == account_id,
        )
        .correlate(Comment)
        .scalar_subquery()
    )
    return (
        select(Comment)
        .where(Comment.assignee_id == account_id, Comment.resolved_at.is_(None))
        .where(or_(settled.is_(None), _assignment_id() > settled))
    )


def pending_asks(session: Session, account_id: str) -> list[Comment]:
    """The open Asks the work loop would still hand the account (`_pending_asks`: unresolved,
    and not consumed by one of its finishes), oldest first. The agent plan reads this."""
    return list(session.scalars(_pending_asks(account_id).order_by(Comment.created_at, Comment.id)))


def turn_asks(session: Session, account_id: str, item_id: str) -> list[Comment]:
    """Pending Asks in the current take, excluding assignments arriving during the run."""
    start = _turn_start(session, account_id, item_id)
    q = _pending_asks(account_id).where(Comment.item_id == item_id)
    if start is not None:
        q = q.where(_assignment_id() < start.id)
    return list(session.scalars(q.order_by(Comment.created_at, Comment.id)))


def _turn_start(session: Session, account_id: str, item_id: str) -> Event | None:
    last = session.scalar(
        select(Event)
        .where(
            Event.item_id == item_id,
            Event.actor_id == account_id,
            Event.type.in_(["item.work_started", "item.work_finished"]),
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    return last if last is not None and last.type == "item.work_started" else None


def _is_assigned(session: Session, item_id: str, account_id: str) -> bool:
    return (
        session.scalar(
            select(ItemAssignee.item_id).where(
                ItemAssignee.item_id == item_id, ItemAssignee.account_id == account_id
            )
        )
        is not None
    )


def _is_asked(session: Session, item_id: str, account_id: str) -> bool:
    return (
        session.scalar(_asked_items(account_id).where(Comment.item_id == item_id).limit(1))
        is not None
    )


def _why(session: Session, item: Item, account_id: str) -> str:
    if item.waiting_on_account_id == account_id:
        return "waiting_on_you"
    if _is_assigned(session, item.id, account_id):
        return "assigned"
    return "asked" if _is_asked(session, item.id, account_id) else "pool"


def _asked_only(session: Session, item_id: str, account_id: str) -> bool:
    """The account holds this task only because it was asked something on it (an open Ask),
    not as an assignee: taking it does not assign it, and `done` answers instead of completing."""
    return not _is_assigned(session, item_id, account_id) and _is_asked(
        session, item_id, account_id
    )


def _waits_on_asker(session: Session, asks: list[Any], waiting_on: str) -> bool:
    """Does `waiting_on` name the author of one of the Asks this turn took?"""
    target = resolve_account(session, waiting_on)
    return any(ask.author_id == target.id for ask in asks)


def is_takeable(session: Session, account_id: str, item_id: str, *, pool: bool = False) -> bool:
    """Would `next_task` consider this item for the account right now?"""
    q = eligible(session, account_id, pool=pool, item_id=item_id).limit(1)
    return session.scalar(q.with_only_columns(Item.id)) is not None


def remaining(session: Session, account_id: str, **scope: Any) -> int:
    """How many other tasks the account could take now."""
    sub = eligible(session, account_id, **scope).order_by(None).with_only_columns(Item.id)
    return session.scalar(select(func.count()).select_from(sub.subquery())) or 0


# --- Take, keep, give back ------------------------------------------------------------------


def lease_view(lease: WorkLease) -> dict[str, Any]:
    return {
        "run_id": lease.run_id,
        "item_id": lease.item_id,
        "account_id": lease.account_id,
        "claimed_at": lease.claimed_at,
        "until": lease.until,
        "from_pool": lease.from_pool,
    }


def _assignee_ids(session: Session, item_id: str) -> list[str]:
    return list(
        session.scalars(select(ItemAssignee.account_id).where(ItemAssignee.item_id == item_id))
    )


def take(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    lease: int | None = None,
    note: str | None = None,
    progress: int | None = None,
    url: str | None = None,
    thread: Any = None,
    now: datetime | None = None,
    new_turn: bool = True,
    run_id: str | None = None,
) -> WorkLease:
    """Claim one item for `actor` with a lease (409 `Conflict` if another account holds it)."""
    seconds = lease_seconds(lease)
    now = now or utcnow()
    actor = reports.with_thread_ref(session, actor, reports.check_thread(thread))
    # The claim's events are the record; the lease rides on the same commit as bookkeeping.
    with apply_change(session, actor) as cx:
        existing = session.get(WorkLease, item_id, populate_existing=True)
        if existing is not None:
            if new_turn:
                raise Conflict("work already has an execution; resume requires run_id proof")
            if not new_turn:
                _holding(session, actor, get_item(session, item_id), run_id, now)
        was_assigned = actor.account_id in _assignee_ids(session, item_id)
        # Asked something on a task it is not assigned to: hold it without becoming an assignee.
        asked = not was_assigned and _is_asked(session, item_id, actor.account_id)
        item = reports.report(
            session,
            actor,
            item_id,
            state="working",
            note=note,
            progress=progress,
            comment=False,
            claim=True,
            url=url,
            no_assign=asked,
            thread=thread,
            run_id=run_id,
            _taking=new_turn,
        )
        if item.run_requested_at is not None:
            item.run_requested_at = None  # this take is the run that was asked for
        row = session.get(WorkLease, item.id)
        if row is None:
            row = WorkLease(item_id=item.id, account_id=actor.account_id, from_pool=False)
            session.add(row)
            row.claimed_at = now
        elif row.account_id != actor.account_id or row.until < now:
            row.account_id, row.from_pool, row.claimed_at = actor.account_id, False, now
        row.until = now + timedelta(seconds=seconds)
        row.from_pool = (row.from_pool or not was_assigned) and not asked
        if new_turn:
            row.run_id = secrets.token_urlsafe(32)
            row.asked_only = asked or item.completed_at is not None
            cx.record(
                "item.work_started",
                item_id=item.id,
                after={"run_digest": _digest(row.run_id), "asked_only": asked},
            )
    return row


def context(session: Session, item_id: str) -> list[dict[str, Any]]:
    """The item's last comments and events, oldest first: what an agent reads before starting."""
    from slate.service import activity

    return activity.activity(session, item_id, limit=CONTEXT_ENTRIES)


def next_task(
    session: Session,
    actor: Actor,
    *,
    lease: int | None = None,
    pool: bool = False,
    list_id: str | None = None,
    section_id: str | None = None,
    tag: str | None = None,
    resume: bool = True,
    run_id: str | None = None,
    note: str | None = None,
    item_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Take the best task for the actor's account (module docstring) and return
    `{item, lease, resumed, why}`; None when nothing is available. With `item_id`, that task
    or nothing (a runner answering a Run now hint must run the task that was pressed)."""
    seconds = lease_seconds(lease)
    now = now or utcnow()
    me = actor.account_id
    scope = {
        "pool": pool,
        "list_id": list_id,
        "section_id": section_id,
        "tag": tag,
        "item_id": item_id,
    }
    if run_id:
        if not resume or not item_id:
            raise Invalid("run_id resume requires resume=true and item_id")
        row = heartbeat(session, actor, item_id, lease=seconds, run_id=run_id, now=now)
        return _taken(session, get_item(session, item_id), row, True)
    tried: set[str] = set()
    for _ in range(TRIES):
        q = eligible(session, me, now=now, **scope)
        if tried:
            q = q.where(Item.id.not_in(tried))
        item = session.scalars(q.limit(1)).first()
        if item is None:
            return None
        tried.add(item.id)
        why = _why(session, item, me)
        try:
            row = take(session, actor, item.id, lease=seconds, note=note, now=now)
        except Conflict:
            continue  # another agent took it between the read and the lock: try the next one
        return _taken(session, item, row, False, why)
    return None


def _taken(
    session: Session, item: Item, row: WorkLease, resumed: bool, why: str | None = None
) -> dict[str, Any]:
    session.refresh(item)
    return {
        "item": item,
        "lease": lease_view(row),
        "resumed": resumed,
        "why": why or _why(session, item, row.account_id),
    }


def _already_answered(session: Session, item_id: str, account_id: str, state: str) -> bool:
    """Did the agent already report `state` with a comment of its own during this turn (its own
    Slate tool, before its runner reports the same result)? Then the runner's answer would be a
    second copy. The turn starts at the agent's last take: its report moving into `working`."""
    reports_ = session.scalars(
        select(Event)
        .where(
            Event.item_id == item_id,
            Event.actor_id == account_id,
            Event.type == "item.reported",
        )
        .order_by(Event.at.desc(), Event.id.desc())
        .limit(200)
    ).all()
    start = None
    reported = False
    for ev in reports_:  # newest first
        after = ev.after_json if isinstance(ev.after_json, dict) else {}
        before = ev.before_json if isinstance(ev.before_json, dict) else {}
        if after.get("state") == state:
            reported = True
        if after.get("state") == "working" and before.get("state") != "working":
            start = ev.at
            break
    if start is None or not reported:
        return False
    spoke = session.scalar(
        select(Comment.id)
        .where(
            Comment.item_id == item_id,
            Comment.author_id == account_id,
            Comment.created_at >= start,
        )
        .limit(1)
    )
    return spoke is not None


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _receipt(
    session: Session, actor: Actor, type_: str, run_id: str | None, payload: dict[str, Any]
) -> dict[str, Any] | None:
    if not run_id:
        return None
    event = session.scalar(
        select(Event)
        .where(
            Event.type == type_,
            Event.actor_id == actor.account_id,
            Event.after_json["run_digest"].as_string() == _digest(run_id),
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    if event is None:
        return None
    after = event.after_json
    if after.get("request_digest") != _digest(payload):
        raise Conflict("run already finished with a different result")
    return after


def _holding(
    session: Session,
    actor: Actor,
    item: Item,
    run_id: str | None = None,
    now: datetime | None = None,
) -> WorkLease:
    """Check proof and current control state under apply_change's write lock."""
    row = session.get(WorkLease, item.id, populate_existing=True)
    if (
        row is None
        or not run_id
        or not row.run_id
        or not secrets.compare_digest(row.run_id, run_id)
        or row.account_id != actor.account_id
        or row.quarantined
        or row.until < (now or utcnow())
    ):
        raise Conflict("current run_id proof required; execution lease lost or expired")
    access.check_object(session, item)
    status = session.get(Status, item.status_id) if item.status_id else None
    lst = session.get(List, item.list_id)
    section = session.get(Section, lst.section_id) if lst else None
    if (
        item.archived_at
        or lst is None
        or lst.archived_at
        or section is None
        or section.archived_at
        or (status and status.kind == StatusKind.cancelled)
        or (not row.asked_only and status and status.kind == StatusKind.done)
        or (not row.asked_only and not _is_assigned(session, item.id, actor.account_id))
    ):
        raise Conflict("work was closed, archived or unassigned; stop execution")
    return row


def heartbeat(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    run_id: str | None = None,
    lease: int | None = None,
    note: str | None = None,
    progress: int | None = None,
    url: str | None = None,
    thread: Any = None,
    now: datetime | None = None,
) -> WorkLease:
    """Keep holding the item: the lease runs `lease` seconds from now. With `note`, `progress`,
    a new `url` or a new `thread` it is also a `working` report (the badge updates live; no
    comment).
    `Conflict` when the lease was lost: another account has taken the task, so stop working
    on it."""
    seconds = lease_seconds(lease)
    now = now or utcnow()
    thread = reports.check_thread(thread)
    actor = reports.with_thread_ref(session, actor, thread)
    with apply_change(session, actor):
        item = get_item(session, item_id)
        session.refresh(item)
        row = _holding(session, actor, item, run_id, now)
        url = reports.check_url(url)
        news = url is not None and url != item.agent_url
        if thread is not None and thread != reports.thread_of(session, item.id, actor.account_id):
            news = True
        note = (note or "").strip() or None
        if note == item.agent_note and progress in (None, item.agent_progress):
            note, progress = None, None
        elif note is None and progress == item.agent_progress:
            progress = None
        if (
            note is not None
            or progress is not None
            or news
            or item.agent_state != AgentState.working
        ):
            reports.report(
                session,
                actor,
                item.id,
                state="working",
                comment=False,
                note=note if note is not None else item.agent_note,
                progress=progress if progress is not None else item.agent_progress,
                url=url,
                thread=thread,
                run_id=run_id,
            )
        row.until = now + timedelta(seconds=seconds)
    return row


def finish(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    outcome: str,
    run_id: str | None = None,
    note: str | None = None,
    comment: bool = True,
    progress: int | None = None,
    status: str | None = None,
    hand_to: str | None = None,
    waiting_on: str | None = None,
    url: str | None = None,
    reply: str | None = None,
    thread: Any = None,
) -> Item:
    """Report back on a task in one transaction and give up the lease.

    `outcome`: `done` (completes it, or moves it to `status`), `review` (badge stays: ready for
    a person), `blocked` (with `waiting_on`: lands in that person's Today), `release` (give it
    up: you are un-assigned, so it goes back to its other assignees or the pool). `note` is
    posted as a comment (unless `comment=false`); `status` sets a
    status by name or id; `hand_to` (a handle) assigns the task to that account in place of
    you, so an agent's next `/work/next` picks it up. `reply` is your answer for this turn,
    posted as one plain comment by you instead of the note (card conversation); `thread` is your
    own session for this task (service/reports.py). If you already reported this outcome with a
    comment of your own since you took the task, nothing is posted again (one answer per turn).
    Agent finishes consume only Ask assignments present at take, for every outcome, without
    hiding or resolving them. The existing asked-only `done` exception explicitly resolves
    those taken Asks instead of completing the task. Human finishes never resolve Asks.
    A plain comment or report is not a work finish. Legacy takes without a boundary settle
    no Asks; a subsequent take establishes one."""
    if outcome not in OUTCOMES:
        raise Invalid(f"outcome must be one of {', '.join(OUTCOMES)}")
    if hand_to is not None and outcome == "done":
        raise Invalid("hand_to goes with review, blocked or release, not done")
    item = get_item(session, item_id)
    target = resolve_account(session, hand_to) if hand_to else None
    if target is not None and target.id == actor.account_id:
        raise Invalid("hand_to names you; pick another account")
    state = {"release": "idle"}.get(outcome, outcome)
    payload = dict(
        item_id=item_id,
        outcome=outcome,
        note=note,
        comment=comment,
        progress=progress,
        status=status,
        hand_to=hand_to,
        waiting_on=waiting_on,
        url=url,
        reply=reply,
        thread=thread,
    )
    actor = reports.with_thread_ref(session, actor, reports.check_thread(thread))
    with apply_change(session, actor) as cx:
        session.refresh(item)
        access.check_object(session, item)
        receipt = _receipt(session, actor, "item.work_finished", run_id, payload)
        session.info["work_finish_replayed"] = receipt is not None
        if receipt is not None:
            return item
        row = session.get(WorkLease, item.id, populate_existing=True)
        if row is not None or run_id:
            row = _holding(session, actor, item, run_id)
        answering = (
            row.asked_only if row is not None else _asked_only(session, item.id, actor.account_id)
        )
        if comment and _already_answered(session, item.id, actor.account_id, state):
            comment, reply = False, None
        start = _turn_start(session, actor.account_id, item.id)
        asks = turn_asks(session, actor.account_id, item.id) if start is not None else []
        if (
            answering
            and state == "blocked"
            and waiting_on
            and not _waits_on_asker(session, asks, waiting_on)
        ):
            # Only asked, not its card: it may wait on whoever asked it, nobody else. Blocked on
            # a third person ("someone else's job") is just its answer: no badge, no waiting-on.
            state, waiting_on = "idle", None
        reports.report(
            session,
            actor,
            item.id,
            state=state,
            note=note,
            progress=progress,
            comment=comment and target is None,
            complete=status is None and not answering,
            _finishing=True,
            run_id=run_id,
            waiting_on=waiting_on,
            url=url,
            reply=reply,
            thread=thread,
        )
        changes: dict[str, Any] = {}
        if status is not None:
            changes["status"] = status
        current = _assignee_ids(session, item.id)
        if target is not None:
            keep = [a for a in current if a != actor.account_id]
            changes["assignees"] = [*keep, *([] if target.id in keep else [target.id])]
            body = f"**Handed to @{target.handle}**" + (f" · {note}" if note else "")
            if comment:
                comments.add_comment(session, actor, item.id, body_md=body)
        elif outcome == "release" and actor.account_id in current:
            changes["assignees"] = [a for a in current if a != actor.account_id]
        if changes:
            update_item(session, actor, item.id, changes)
        if start is not None or run_id:
            if answering and outcome == "done" and _is_agent(session, actor.account_id):
                for ask in asks:
                    comments.resolve_comment(session, actor, ask.id)
            cx.record(
                "item.work_finished",
                item_id=item.id,
                after={
                    "through": start.id if start else None,
                    "outcome": outcome,
                    "run_digest": _digest(run_id),
                    "request_digest": _digest(payload),
                },
            )
        if row is not None:
            session.delete(row)
    session.refresh(item)
    return item


def agent_assignees(session: Session, item_id: str) -> list[str]:
    """The item's assignees that are agent accounts, by id."""
    return list(
        session.scalars(
            select(ItemAssignee.account_id)
            .join(Account, Account.id == ItemAssignee.account_id)
            .where(ItemAssignee.item_id == item_id, Account.kind == AccountKind.agent)
            .order_by(ItemAssignee.account_id)
        )
    )


def nudge_fields(session: Session, agent_ids: list[str]) -> dict[str, Any]:
    """For an `item.run_requested` event: the agents among `agent_ids` that no runner wakes
    (`wake` pull or manual) and the people to tell instead. `{}` when every agent is `push`.

    `nudge` is `[{id, name, wake}]`; `notify` is the account ids whose inbox (and phone, by
    their push settings) gets a `nudge` entry: each agent's `prefs.owner` (a handle) when it
    names an active person, else the workspace owners."""
    agents = [
        a
        for a in session.scalars(select(Account).where(Account.id.in_(agent_ids)))
        if (a.wake or "push") != "push"
    ]
    if not agents:
        return {}
    notify: list[str] = []
    for agent in sorted(agents, key=lambda a: a.id):
        handle = (agent.prefs_json or {}).get("owner")
        owner = (
            session.scalar(
                select(Account).where(
                    Account.handle == str(handle).lstrip("@"),
                    Account.kind == AccountKind.person,
                    Account.disabled_at.is_(None),
                )
            )
            if handle
            else None
        )
        ids = (
            [owner.id]
            if owner is not None
            else list(
                session.scalars(
                    select(Account.id).where(
                        Account.role == Role.owner, Account.disabled_at.is_(None)
                    )
                )
            )
        )
        notify += [i for i in ids if i not in notify]
    return {
        "nudge": [
            {"id": a.id, "name": a.display_name, "wake": a.wake}
            for a in sorted(agents, key=lambda a: a.id)
        ],
        "notify": notify,
    }


def request_run(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    reason: str = "now",
    now: datetime | None = None,
) -> Any:
    """Ask the item's agent assignees to run it now: one `item.run_requested` event
    (`after = {for, reason}`), which reaches their agent streams as a `work` hint and webhooks
    like any event. Agents whose `wake` is pull or manual have no runner to hint: the event also
    carries `nudge` and `notify` (nudge_fields), and those people get a `nudge` inbox entry.
    `Invalid` when no agent is assigned, or it is done, cancelled or archived. `Conflict` (409)
    while an agent is working on it (a live lease, or with none a fresh `working` badge): the
    message says who and for how long, so the board can say so instead of doing nothing."""
    item = get_item(session, item_id)
    if item.archived_at is not None:
        raise Invalid("this item is archived")
    status = session.get(Status, item.status_id) if item.status_id else None
    if status is not None and status.kind in (StatusKind.done, StatusKind.cancelled):
        raise Invalid(f"this item is {status.kind.value}; reopen it to run it")
    agents = agent_assignees(session, item.id)
    if not agents:
        raise Invalid("no agent is assigned to this item: assign one to run it")
    for handle in agents:  # a guest cannot start a workspace agent (service/guests.py)
        guests.refuse(session, actor.account_id, session.get(Account, handle))
    now = now or utcnow()
    _refuse_if_working(session, item, now)
    nudge = nudge_fields(session, agents)  # read before the write: a query here would flush
    with apply_change(session, actor) as cx:
        before = item.run_requested_at
        session.refresh(item)
        _refuse_if_working(session, item, now)
        if session.get(WorkLease, item.id) is not None:
            raise Conflict("execution state is unknown; reconcile the current run before retrying")
        item.run_requested_at = now
        event = cx.record(
            "item.run_requested",
            item_id=item.id,
            field="run_requested_at",
            before=before,
            after={"for": agents, "reason": reason, **nudge},
            covers=[item],
        )
    return event


def _refuse_if_working(session: Session, item: Item, now: datetime) -> None:
    if item.agent_state != AgentState.working or item.agent_state_by is None:
        return
    lease = session.get(WorkLease, item.id)
    if lease is not None and lease.account_id == item.agent_state_by:
        if lease.until < now:
            return
        since = lease.claimed_at
    else:
        since = item.agent_state_at
        if since is None or now - since >= reports.CLAIM_FRESH:
            return
    who = session.get(Account, item.agent_state_by)
    name = who.display_name if who else "An agent"
    raise Conflict(f"{name} already has it (working {_for_how_long(now - since)})")


def _for_how_long(spent: timedelta) -> str:
    minutes = int(spent.total_seconds() // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min" if minutes % 60 else f"{minutes // 60} h"


def held_by(session: Session, account_id: str) -> list[dict[str, Any]]:
    """The account's live leases, soonest to expire first."""
    rows = session.scalars(
        select(WorkLease).where(WorkLease.account_id == account_id).order_by(WorkLease.until)
    )
    return [{**lease_view(r), "run_id": None} for r in rows]


def expire_leases(session: Session, now: datetime) -> int:
    """Scheduler hook: give back every task whose lease ran out. The agent's badge is cleared
    with an `item.reported` idle event by `system` (`after.by` = the agent, `reason`
    `lease_expired`), and a task the take had assigned from the pool is un-assigned. A lease
    whose badge is gone already (the task was finished or taken over) is just forgotten."""
    from slate.service.due import system_actor

    expired = list(session.scalars(select(WorkLease).where(WorkLease.until < now)))
    if not expired:
        return 0
    actor = system_actor(session)
    released = 0
    for row in expired:
        try:
            released += _expire_lease(session, actor, row, now)
        except Exception:  # noqa: BLE001  # one lease must not keep the others held
            log.exception("expiring lease on %s failed", row.item_id)
            session.rollback()
    return released


def _expire_lease(session: Session, actor: Actor, row: WorkLease, now: datetime) -> int:
    """Quarantine under the write lock: elapsed time cannot prove remote cancellation."""
    with apply_change(session, actor) as cx:
        fresh = session.get(WorkLease, row.item_id, populate_existing=True)
        if fresh is None or fresh.until >= now or fresh.quarantined:
            return 0
        row = fresh
        item = session.get(Item, row.item_id, populate_existing=True)
        if item is None:
            return 0
        before = reports.flag_of(item)
        row.quarantined = True
        if item.agent_state == AgentState.working and item.agent_state_by == row.account_id:
            item.agent_state = AgentState.blocked
            item.agent_note = "Execution lease expired; confirm the runtime stopped before retrying"
            item.agent_state_at = now
        cx.record(
            "item.reported",
            item_id=item.id,
            field="agent_state",
            before=before,
            after={
                **reports.flag_of(item),
                "reason": "lease_expired",
                "reconciliation_required": True,
            },
            covers=[item],
        )
    return 1


def reconcile(
    session: Session, actor: Actor, item_id: str, *, run_id: str, state: str, evidence: str
) -> dict[str, Any]:
    """Attest that the remote execution is terminal; unlock, but do not complete the task.

    Runtime adapters must supply actual cancellation/status evidence, not local process exit.
    An owner can use a retained run proof to reconcile a revoked account's attempt.
    """
    if state not in ("stopped", "completed") or not evidence.strip():
        raise Invalid("terminal runtime state and evidence are required")
    payload = dict(item_id=item_id, state=state, evidence=evidence, reconcile=True)
    with apply_change(session, actor) as cx:
        item = get_item(session, item_id)
        access.check_object(session, item)
        if _receipt(session, actor, "item.work_finished", run_id, payload):
            return {"reconciled": True}
        row = session.get(WorkLease, item_id, populate_existing=True)
        account = session.get(Account, actor.account_id)
        if (
            row is None
            or not row.run_id
            or not secrets.compare_digest(row.run_id, run_id)
            or (row.account_id != actor.account_id and account.role not in (Role.owner, Role.admin))
        ):
            raise Conflict("current run proof required for reconciliation")
        start = _turn_start(session, row.account_id, item_id)
        if item.agent_state_by == row.account_id:
            for field in (
                "agent_state",
                "agent_state_by",
                "agent_state_at",
                "agent_note",
                "agent_progress",
                "agent_url",
            ):
                setattr(item, field, None)
        cx.record(
            "item.work_finished",
            item_id=item_id,
            after={
                "through": start.id if start else None,
                "outcome": "release",
                "reconciled": True,
                "expired": bool(row.quarantined),
                "runtime_state": state,
                "evidence": evidence,
                "run_digest": _digest(run_id),
                "request_digest": _digest(payload),
            },
            covers=[item],
        )
        session.delete(row)
    return {"reconciled": True}


def _person_clears_expired(
    session: Session, cx: Any, actor: Actor, item: Item, row: WorkLease
) -> bool:
    """A person's Clear on an expired run's badge is the confirmation the quarantine asks for.

    Only a quarantined lease (it expired; the runner lost it) held by someone else, and only
    for a person account: an agent still needs its run proof (`reconcile`), and a live lease is
    never overridden. Records `item.work_finished` (reconciled, by the person) and drops the
    lease, so the agent's next take answers the Asks that waited. Runs inside the caller's
    `apply_change`. Returns False (nothing done) when it does not apply."""
    if not row.quarantined or row.account_id == actor.account_id:
        return False
    account = session.get(Account, actor.account_id)
    if account is None or account.kind != AccountKind.person:
        return False
    access.check_object(session, item)
    start = _turn_start(session, row.account_id, item.id)
    cx.record(
        "item.work_finished",
        item_id=item.id,
        after={
            "through": start.id if start else None,
            "outcome": "release",
            "reconciled": True,
            "expired": True,
            "runtime_state": "stopped",
            "evidence": f"{account.handle} cleared the expired run's badge",
            "run_digest": _digest(row.run_id) if row.run_id else None,
        },
        covers=[item],
    )
    session.delete(row)
    return True


# --- The agent's live stream --------------------------------------------------------------

# Item events that never make a task takeable: no `work` hint is worked out for them.
# `item.due` and `item.run_requested` do: they open the due gate (module docstring).
NO_HINT_TYPES = frozenset(
    {
        "item.commented",
        "item.mentioned",
        "item.watched",
        "item.unwatched",
        "item.reminder",
        "item.reminder_set",
        "item.reminder_removed",
        "item.due_soon",
        "item.overdue",
    }
)


# Events that ask an agent to run the task now: hinted every time, not once per task.
WAKE_TYPES = frozenset({"item.run_requested", "item.due", "comment.assigned"})
# A chat mention of the agent (service/chatwake.py): a `work` hint with channel and thread.
CHAT_WAKE_TYPE = "message.mentioned_agent"
# A doc mention of the agent (service/docwake.py): a `work` hint with doc_id and thread_id.
DOC_WAKE_TYPE = "doc.mentioned_agent"


def may_free_work(event_type: str) -> bool:
    """Could an event of this type have made some task takeable (assigned, unblocked,
    released, reopened, moved, restored...)? Cheap filter before `is_takeable`."""
    if event_type in WAKE_TYPES:
        return True
    return event_type.startswith("item.") and event_type not in NO_HINT_TYPES


def as_event(payload: dict[str, Any]) -> Any:
    """A bus payload with the attributes `checkin.why` reads off an `Event`."""
    return SimpleNamespace(
        id=payload["id"],
        type=payload["type"],
        item_id=payload.get("item_id"),
        subject_id=payload.get("subject_id"),
        actor_id=payload.get("actor_id"),
        field=payload.get("field"),
        before_json=payload.get("before"),
        after_json=payload.get("after"),
    )
