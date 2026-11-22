"""The agent work loop: take the next task, keep it while working, report back in one call.

    next_task(session, actor, account, lease_seconds=900)   # REST POST /work/next, MCP slate_work
    heartbeat(session, actor, item_id)                       # keep the lease alive
    finish(session, actor, item_id, outcome="done", note="Sent")

A take is an ordinary claim (`reports.report(state="working", claim=True)`): one
`item.reported` event, the card's live badge, the agent added to the assignees. On top of it
the agent holds a `WorkLease` (models.WorkLease): until `until` nobody else can take the task;
`heartbeat` moves `until` without an event (bookkeeping, like a token's `last_used_at`); the
scheduler hook `expire_leases` quarantines a lease that ran out: the run may still be going, so
the task stays held (the badge turns `blocked`, assignees unchanged) until the agent reconciles
with its run proof (`reconcile`) or a person clears the badge (`_person_clears_expired`).

Which task: `eligible()` in SQL, best first.

- Scope: open tasks assigned to the account or waiting on it; with `pool`, also tasks with no
  assignee in lists the caller may edit. Narrowed by `list_id`, `section_id`, `tag`.
- Skipped: done/cancelled, archived (or in an archived list or section), blocked by an open
  task (`links.blocked_item_ids()`, the same rule as the item view's `blocked`), not started yet
  (`start_at` in the future), due-gated (below), waiting on someone else, flagged `review`
  (waiting on a person), reported `blocked` by you in the last `BLOCKED_PAUSE` (unless someone
  has since asked you something on it: a reply, below), and held by another account (a live
  lease, or with no lease a badge younger than `reports.CLAIM_FRESH`; a parked `blocked` or
  `review` badge with no lease does not hold the card against an Ask for you; a `failed`
  badge holds nothing), `failed` (the run broke) until its retry time comes, and your own
  last finish on it `answered` (you replied; nothing is left for you until someone asks you
  something or presses Run now). A Run now
  pressed after the last report (the scheduler's retry is one) overrides waiting on someone,
  `review`, `failed` and the blocked pause: whoever pressed it wants the agent to go on. A
  pending Ask for the account (not consumed by a work finish) overrides the same and a done
  status too: a question is answered now, whatever the card says.

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

from slate import dates, liveactivity
from slate.models import (
    Account,
    AccountKind,
    AgentState,
    Comment,
    CommentRun,
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
from slate.service.errors import Conflict, Invalid, NotFound
from slate.service.items import PRIORITY_RANK, get_item, resolve_account, update_item
from slate.service.lists import WORK_LIST

log = logging.getLogger(__name__)

DEFAULT_LEASE = 15 * 60
MIN_LEASE = 60
MAX_LEASE = 24 * 60 * 60
TRIES = 5  # candidates tried per call when other agents win the race for them
OUTCOMES = ("done", "review", "later", "ask", "answered", "failed", "blocked", "release")
CONTEXT_ENTRIES = 10
# A task you reported blocked stays out of your `next` this long (unless the badge is cleared),
# so a failing task is not retried at once; one waiting on someone else stays out until then.
BLOCKED_PAUSE = timedelta(hours=1)
# A `failed` finish is offered to the same agent again this long after its first and second
# consecutive failure (the scheduler's `due.retry_failed_runs`); the third gives up
# (`item.run_failed`, told to people once). Run now starts the count over.
RETRY_AFTER = (timedelta(minutes=5), timedelta(minutes=30))
GIVE_UP_AFTER = len(RETRY_AFTER) + 1
# A `working` badge with no lease and no report for this long is cleared (`due.clear_stale_badges`).
STALE_AFTER = timedelta(minutes=30)
# An open card its agent last finished `answered` this long ago is poked once
# (`due.poke_stalled`); a poked run that answers again marks the card stalled.
STALL_AFTER = timedelta(hours=4)
# A lease is `working` until its time runs out, then `expired`: the task stays held until the
# agent reconciles or a person clears the badge (`docs/state-machines.md`).
LEASE_STATES = ("working", "expired")


def lease_state(row: WorkLease, now: datetime | None = None) -> str:
    """The one rule for a lease's state. The clock decides, not only the scheduler's
    `quarantined` mark, so a lease is `expired` from the moment `until` passes."""
    return "expired" if row.quarantined or row.until < (now or utcnow()) else "working"


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
    # Reuse one access-filtered Ask query across the eligibility checks. Inlining it in
    # every branch repeats the visibility binds and can exceed SQLite's parameter limit.
    asks = _asked_items(account_id).cte()
    asked_ids = select(asks.c.item_id)
    mine = (
        select(ItemAssignee.item_id.label("id"))
        .where(ItemAssignee.account_id == account_id)
        .union(
            select(Item.id).where(Item.waiting_on_account_id == account_id),
            asked_ids,
        )
        .subquery()
    )
    is_mine = Item.id.in_(select(mine.c.id))
    # An open Ask for this account (a question on the card): it is answered now, even on a
    # card that is ready for review, waiting on someone or already done (card chat).
    asked = Item.id.in_(asked_ids)
    q = (
        select(Item)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .outerjoin(lease, lease.item_id == Item.id)
        .where(
            Item.kind == ItemKind.task,
            Item.archived_at.is_(None),
            Item.parked_until.is_(None),
            List.archived_at.is_(None),
            WORK_LIST,
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
        Item.agent_state != AgentState.failed,
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
    # Only an explicit Run now can override the start date. A scheduled retry also sets
    # run_requested_at, so check the reason on the request still waiting to be taken.
    requested_now = (
        select(Event.after_json["reason"].as_string())
        .where(
            Event.item_id == Item.id,
            Event.type == "item.run_requested",
            Event.after_json["reason"].as_string().in_(("now", "retry")),
        )
        .order_by(Event.id.desc())
        .limit(1)
        .correlate(Item)
        .scalar_subquery()
    ) == "now"
    q = q.where(
        lease.item_id.is_(None),  # expired is unknown, not proof of remote cancellation
        or_(is_mine, and_(unassigned, true() if pool else false())),
        Item.id.not_in(links.blocked_item_ids()),
        or_(Item.start_at.is_(None), Item.start_at <= now, and_(pressed, requested_now)),
        or_(
            Item.waiting_on_account_id.is_(None),
            Item.waiting_on_account_id == account_id,
            pressed,
            asked,
        ),
        or_(Item.agent_state.is_(None), Item.agent_state != AgentState.review, pressed, asked),
        or_(Item.agent_state.is_(None), Item.agent_state != AgentState.failed, pressed, asked),
        or_(_last_outcome(account_id) != "answered", pressed, asked, _stall_poke(account_id)),
        pressed
        | not_(
            and_(
                Item.agent_state.is_not(None),
                Item.agent_state == AgentState.blocked,
                Item.agent_state_by == account_id,
                Item.agent_state_at.is_not(None),
                Item.agent_state_at > now - BLOCKED_PAUSE,
                # An open Ask for you (a reply to your question) ends the pause at once.
                not_(asked),
            )
        ),
        not_(held_by_other),
        not_(i_hold),
    )
    if _is_agent(session, account_id):
        q = q.where(_run_time_has_come(account_id, now, asked))
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


def _last_outcome(account_id: str) -> Any:
    """SQL: the outcome of the account's newest finish on the item ('' when none)."""
    return func.coalesce(
        select(Event.after_json["outcome"].as_string())
        .where(
            Event.type == "item.work_finished",
            Event.item_id == Item.id,
            Event.actor_id == account_id,
        )
        .order_by(Event.id.desc())
        .limit(1)
        .correlate(Item)
        .scalar_subquery(),
        "",
    )


def _stall_poke(account_id: str) -> Any:
    """SQL: Slate's stalled poke for this account on the item (`due.poke_stalled`), newer than
    the account's newest finish there: the card is offered to it, and only it, once."""
    targets = func.json_each(Event.after_json, "$.for").table_valued("value")
    finished = (
        select(func.max(Event.id))
        .where(
            Event.type == "item.work_finished",
            Event.item_id == Item.id,
            Event.actor_id == account_id,
        )
        .correlate(Item)
        .scalar_subquery()
    )
    return (
        select(Event.id)
        .where(
            Event.type == "item.run_requested",
            Event.item_id == Item.id,
            Event.after_json["reason"].as_string() == "stalled",
            select(targets.c.value).where(targets.c.value == account_id).exists(),
            or_(finished.is_(None), Event.id > finished),
        )
        .correlate(Item)
        .exists()
    )


def _is_agent(session: Session, account_id: str) -> bool:
    kind = session.scalar(select(Account.kind).where(Account.id == account_id))
    return kind == AccountKind.agent


def _run_time_has_come(account_id: str, now: datetime, asked: Any) -> Any:
    """The due gate as a SQL condition (module docstring): true for tasks the agent may take.
    An open Ask (assigned comment) for the agent opens it: a question is answered now."""
    assigned = Item.id.in_(
        select(ItemAssignee.item_id).where(ItemAssignee.account_id == account_id)
    )
    early = dates.early_date_only_due(now)
    return or_(
        not_(assigned),
        asked,
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
    """Pending Asks in the current take: the ones it started with and the ones it read while
    running (`_turn_through`), not the ones that arrived and were never handed to it."""
    start = _turn_start(session, account_id, item_id)
    q = _pending_asks(account_id).where(Comment.item_id == item_id)
    if start is not None:
        q = q.where(_assignment_id() <= _turn_through(session, account_id, item_id, start))
    return list(session.scalars(q.order_by(Comment.created_at, Comment.id)))


# --- While a run is going: new messages and Stop ----------------------------------------------
#
# A person's Ask that arrives while the agent holds the card is not a new turn yet: the run's
# heartbeat answers with it (`turn_control` -> `messages`), the runner passes it to the running
# agent and says so (`heartbeat(seen=...)` records `comment.seen`), and the finish settles what
# was read (`_turn_through`). One never read stays pending and is the agent's next turn, as
# before. Stop is one `item.stop_requested` event for the account holding the lease; the same
# heartbeat answer carries it (`stop`), and the agent's own finish ends the run.

STOP_TYPE = "item.stop_requested"
SEEN_TYPE = "comment.seen"


def _seen_ids(session: Session, account_id: str, item_id: str, start: Event) -> set[str]:
    return set(
        session.scalars(
            select(Event.subject_id).where(
                Event.type == SEEN_TYPE,
                Event.item_id == item_id,
                Event.actor_id == account_id,
                Event.id > start.id,
            )
        )
    )


def _arrived(session: Session, account_id: str, item_id: str, start: Event) -> list[Any]:
    """`(comment, assignment event id)` for the account's pending Asks addressed to it after
    the take, in the order they were addressed."""
    assigned = _assignment_id().label("assigned")
    rows = session.execute(
        _pending_asks(account_id)
        .add_columns(assigned)
        .where(Comment.item_id == item_id, _assignment_id() > start.id)
        .order_by(assigned)
    )
    return [(row[0], row[1]) for row in rows]


def _turn_through(session: Session, account_id: str, item_id: str, start: Event) -> str:
    """The run's settlement cursor: its take, moved past each message it read mid-run, in
    order, stopping at the first one it never read (that one is still a turn to come). A Stop
    settles everything sent before it: the person wrote those and then stopped the run, so
    they must not start it again; what they write after Stop is the next turn."""
    through = start.id
    stop = _stop_of(session, account_id, item_id)
    if stop is not None:
        through = stop.id
    seen = _seen_ids(session, account_id, item_id, start)
    if not seen:
        return through
    for comment, assigned in _arrived(session, account_id, item_id, start):
        if assigned > through and comment.id not in seen:
            break
        through = max(through, assigned)
    return through


def turn_messages(session: Session, account_id: str, item_id: str) -> list[Comment]:
    """The Asks addressed to the account since it took the item that it has not marked seen."""
    start = _turn_start(session, account_id, item_id)
    if start is None:
        return []
    seen = _seen_ids(session, account_id, item_id, start)
    return [c for c, _ in _arrived(session, account_id, item_id, start) if c.id not in seen]


def _stop_of(session: Session, account_id: str, item_id: str) -> Event | None:
    """The stop request against the account's current run on the item, or None."""
    start = _turn_start(session, account_id, item_id)
    if start is None:
        return None
    for event in session.scalars(
        select(Event)
        .where(Event.type == STOP_TYPE, Event.item_id == item_id, Event.id > start.id)
        .order_by(Event.id.desc())
    ):
        if account_id in ((event.after_json or {}).get("for") or []):
            return event
    return None


def turn_control(session: Session, account_id: str, item_id: str) -> dict[str, Any]:
    """What the running agent should know now (the heartbeat's answer): `stop` (who asked the
    run to stop and when, or None) and `messages` (comment views, oldest first)."""
    stop = _stop_of(session, account_id, item_id)
    who = session.get(Account, stop.actor_id) if stop is not None else None
    return {
        "stop": {"by": who.handle if who else None, "by_id": stop.actor_id, "at": stop.at}
        if stop is not None
        else None,
        "messages": comments.comment_views(session, turn_messages(session, account_id, item_id)),
    }


def is_running(
    session: Session, account_id: str, item_id: str, now: datetime | None = None
) -> bool:
    """The account has a live lease on the item (a run of its own is going)."""
    row = session.get(WorkLease, item_id)
    return row is not None and row.account_id == account_id and lease_state(row, now) == "working"


def request_stop(
    session: Session, actor: Actor, item_id: str, *, now: datetime | None = None
) -> Event:
    """Ask the agent working on the item to stop: one `item.stop_requested` event
    (`after.for` = the account holding the lease), which reaches its runner at once (the agent
    stream's `control` message) and on its next heartbeat (`stop`). The run ends when the agent
    finishes it; nothing here takes the lease away. Asking again during the same run returns
    the first request. `Conflict` (409) when no agent is working on it."""
    item = get_item(session, item_id)
    now = now or utcnow()
    with apply_change(session, actor) as cx:
        row = session.get(WorkLease, item.id, populate_existing=True)
        if row is None or lease_state(row, now) == "expired":
            raise Conflict("no agent is working on this item")
        guests.refuse(session, actor.account_id, session.get(Account, row.account_id))
        already = _stop_of(session, row.account_id, item.id)
        if already is not None:
            return already
        return cx.record(STOP_TYPE, item_id=item.id, after={"for": [row.account_id]})


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


def lease_view(lease: WorkLease, now: datetime | None = None) -> dict[str, Any]:
    return {
        "run_id": lease.run_id,
        "item_id": lease.item_id,
        "account_id": lease.account_id,
        "claimed_at": lease.claimed_at,
        "until": lease.until,
        "state": lease_state(lease, now),
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
            row = WorkLease(item_id=item.id, account_id=actor.account_id)
            session.add(row)
            row.claimed_at = now
        elif row.account_id != actor.account_id or row.until < now:
            row.account_id, row.claimed_at = actor.account_id, now
        row.until = now + timedelta(seconds=seconds)
        if new_turn:
            row.run_id = secrets.token_urlsafe(32)
            row.asked_only = asked or item.completed_at is not None
            poke = None
            if item.completed_at is None:  # a closed card taken for an Ask is not the poked run
                last = session.scalar(
                    select(func.max(Event.id)).where(
                        Event.type == "item.work_finished",
                        Event.item_id == item.id,
                        Event.actor_id == actor.account_id,
                    )
                )
                for ev in session.scalars(
                    select(Event)
                    .where(
                        Event.type == "item.run_requested",
                        Event.item_id == item.id,
                        Event.id > (last or ""),
                    )
                    .order_by(Event.id.desc())
                ):
                    after = ev.after_json if isinstance(ev.after_json, dict) else {}
                    if after.get("reason") == "stalled" and actor.account_id in (
                        after.get("for") or []
                    ):
                        poke = ev.id
                        break
            cx.record(
                "item.work_started",
                item_id=item.id,
                after={
                    "run_digest": _digest(row.run_id),
                    "asked_only": asked,
                    **({"poke": poke} if poke else {}),
                },
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


def check_later(item: Item, now: datetime | None = None) -> None:
    """`later` parks a task until its run time, so it needs one still to come: without it the
    due gate would hand the task straight back (or never wake it)."""
    if item.due_at is None:
        raise Invalid("later needs a due time: set the task's due date, then finish later")
    if dates.run_at(item.due_at, item.due_date_only) <= (now or utcnow()):
        raise Invalid("later needs a due time still to come: move the due date, then finish later")


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
        or lease_state(row, now) == "expired"
    ):
        raise Conflict("current run_id proof required; execution lease lost or expired")
    access.check_object(session, item)
    ended = _ended(session, item, row)
    if (
        ended == "archived"
        or (ended and not _closed_it(session, item, row))
        or (not row.asked_only and not _is_assigned(session, item.id, actor.account_id))
    ):
        raise Conflict("work was closed, archived or unassigned; stop execution")
    return row


def _ended(session: Session, item: Item, row: WorkLease) -> str | None:
    """Why the card needs no more work from this lease: `archived` (the card, its list or its
    section), `closed` (cancelled, or done unless the holder took it to answer an Ask on a
    card that was done already), or None."""
    lst = session.get(List, item.list_id)
    section = session.get(Section, lst.section_id) if lst else None
    if item.archived_at or lst is None or lst.archived_at or section is None or section.archived_at:
        return "archived"
    status = session.get(Status, item.status_id) if item.status_id else None
    if status is None:
        return None
    if status.kind == StatusKind.cancelled:
        return "closed"
    return "closed" if status.kind == StatusKind.done and not row.asked_only else None


def _closed_it(session: Session, item: Item, row: WorkLease) -> bool:
    """Did the lease's holder set the card's current status itself? Its own close is its
    answer, not a stop from someone else: the run still heartbeats and reports back."""
    by = session.scalar(
        select(Event.actor_id)
        .where(Event.item_id == item.id, Event.type == "item.status_changed")
        .order_by(Event.id.desc())
        .limit(1)
    )
    return by == row.account_id


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
    seen: list[str] | None = None,
    now: datetime | None = None,
) -> WorkLease:
    """Keep holding the item: the lease runs `lease` seconds from now. With `note`, `progress`,
    a new `url` or a new `thread` it is also a `working` report (the badge updates live; no
    comment). `seen` names messages of this run (`turn_messages`) the running agent has now
    read: each is recorded once as `comment.seen`; any other id is ignored.
    `Conflict` when the lease was lost: another account has taken the task, so stop working
    on it."""
    seconds = lease_seconds(lease)
    now = now or utcnow()
    thread = reports.check_thread(thread)
    actor = reports.with_thread_ref(session, actor, thread)
    with apply_change(session, actor) as cx:
        item = get_item(session, item_id)
        session.refresh(item)
        row = _holding(session, actor, item, run_id, now)
        if seen:
            unread = {c.id for c in turn_messages(session, actor.account_id, item.id)}
            for comment_id in dict.fromkeys(seen):
                if comment_id in unread:
                    cx.record(
                        SEEN_TYPE,
                        item_id=item.id,
                        subject_id=comment_id,
                        after={"comment_id": comment_id, "by": actor.account_id},
                    )
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
    question: str | None = None,
    options: list[str] | None = None,
) -> Item:
    """Report back on a task in one transaction and give up the lease.

    `outcome`: `done` (completes it), `review` (badge stays: something a person must check,
    said in `status`; `waiting_on` names the person, by default whoever asked this turn, else
    the owner), `later` (you did what you can now and the rest waits for the task's due time:
    no badge, nobody waited on, you stay assigned and the due time runs you again; the task
    needs a due time still to come), `ask` (the only way to stop the card on a person: a
    one-line `question`, optional `options`, `waiting_on` a person, by default whoever asked
    this turn, else the owner; service/reports.py), `answered` (your `reply` is posted, your
    badge is cleared, nothing else changes, and the Asks this turn took are resolved),
    `failed` (the machine broke: `note` is stored on `item.work_finished`, never posted; the
    badge is `failed`, nobody is told, and the card is retried, `RETRY_AFTER`; the third
    consecutive failure records `item.run_failed` instead), `blocked` (waiting on an agent or
    on nobody), `release` (give it
    up: you are un-assigned, so it goes back to its other assignees or the pool). `note` is
    posted as a comment (unless `comment=false`); `status` is where the card stands in one line
    (service/reports.py; derived when absent); `hand_to` (a handle) assigns the task to that
    account in place of you, so an agent's next `/work/next` picks it up. `reply` is your answer
    for this turn, posted as one plain comment by you instead of the note (card conversation);
    `thread` is your own session for this task (service/reports.py). If you already reported
    this outcome with a comment of your own since you took the task, nothing is posted again
    (one answer per turn).
    Agent finishes consume only Ask assignments present at take, for every outcome, and every
    agent finish but `failed` and `release` resolves them (`reason: "answered"`): the turn
    answered them. An asked-only `done` answers instead of completing the task. Human finishes
    never resolve Asks. A turn Slate started because the card stalled (`due.poke_stalled`)
    that ends `answered` again sets `stalled` (`item.stalled`); any other finish clears it.
    A plain comment or report is not a work finish. Legacy takes without a boundary settle
    no Asks; a subsequent take establishes one."""
    if outcome not in OUTCOMES:
        raise Invalid(f"outcome must be one of {', '.join(OUTCOMES)}")
    if hand_to is not None and outcome not in ("review", "blocked", "release"):
        raise Invalid(f"hand_to goes with review, blocked or release, not {outcome}")
    if waiting_on is not None and outcome not in ("ask", "blocked", "review"):
        raise Invalid("waiting_on goes with ask, review or blocked")
    if (question is not None or options) and outcome != "ask":
        raise Invalid("question and options go with ask")
    if outcome == "ask" and question is None:
        raise Invalid("ask needs a question: one line")
    if outcome == "answered" and not (reply or "").strip():
        raise Invalid("answered needs your reply")
    if outcome == "failed" and not (note or "").strip():
        raise Invalid("failed needs a note: what broke, in the machine's words")
    item = get_item(session, item_id)
    target = resolve_account(session, hand_to) if hand_to else None
    if target is not None and target.id == actor.account_id:
        raise Invalid("hand_to names you; pick another account")
    if outcome == "later":
        check_later(item)
    state = {"release": "idle", "later": "idle", "answered": "idle", "ask": "blocked"}.get(
        outcome, outcome
    )
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
        # Only an ask carries them, so a retried finish of any other outcome digests the same.
        **({"question": question, "options": options} if outcome == "ask" else {}),
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
            comment = False  # posted already; the reply still says where the card stands
        start = _turn_start(session, actor.account_id, item.id)
        asks = turn_asks(session, actor.account_id, item.id) if start is not None else []
        through = _turn_through(session, actor.account_id, item.id, start) if start else None
        if (
            answering
            and state == "blocked"
            and waiting_on
            and not _waits_on_asker(session, asks, waiting_on)
        ):
            # Only asked, not its card: it may wait on whoever asked it, nobody else. Asking or
            # blocking on a third person ("someone else's job") is just its answer: no badge, no
            # waiting-on, the question (if any) posted as plain words.
            state, waiting_on = "idle", None
            reply, question, options = reply or question, None, None
        if row is not None and state in ("review", "blocked") and _ended(session, item, row):
            # The agent closed the card itself during this run: a closed card carries no flag
            # and waits on nobody. Its answer (an ask's question too) is still posted.
            state, waiting_on = "done", None
            reply, question, options = reply or question, None, None
        reports.report(
            session,
            actor,
            item.id,
            state=state,
            note=reports.first_line(note or "") if outcome == "failed" else note,
            progress=progress,
            comment=comment and target is None and outcome != "failed",
            complete=not answering,
            _finishing=True,
            run_id=run_id,
            waiting_on=waiting_on,
            url=url,
            reply=reply,
            thread=thread,
            question=question,
            options=options,
            status=status,
        )
        # A turn that consumed Slate's stalled poke and answered again, on an open card,
        # stalled; any finish other than answered ends a stall.
        now = utcnow()
        newly_stalled = (
            outcome == "answered"
            and item.stalled_at is None
            and item.parked_until is None
            and (item.start_at is None or item.start_at <= now)
            and (item.due_at is None or item.due_at <= now)
            and session.scalar(select(List.id).where(List.id == item.list_id, WORK_LIST))
            is not None
            and start is not None
            and (start.after_json or {}).get("poke") is not None
            and not _ended(session, item, SimpleNamespace(asked_only=False))
        )
        if newly_stalled:
            item.stalled_at = now  # rides on item.reported
        elif outcome != "answered":
            item.stalled_at = None
        changes: dict[str, Any] = {}
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
        if start is not None or run_id or outcome == "failed":
            if outcome not in ("failed", "release") and _is_agent(session, actor.account_id):
                for ask in asks:  # it answered them; a failed or released run did not
                    comments.resolve_comment(session, actor, ask.id, reason="answered")
            failed = {}
            if outcome == "failed":
                failed = {"note": note, "failures": _failures(session, actor.account_id, item) + 1}
            log = _keep_run_log(session, actor, item.id, start)
            cx.record(
                "item.work_finished",
                item_id=item.id,
                after={
                    "through": through,
                    "outcome": outcome,
                    "run_digest": _digest(run_id),
                    "request_digest": _digest(payload),
                    **failed,
                    # Only a finish that wrote the standing carries it (an agent's).
                    **(
                        {"status": item.standing_line}
                        if item.standing_line and item.standing_by == actor.account_id
                        else {}
                    ),
                    **({"steps": log.steps, "comment_id": log.comment_id} if log else {}),
                },
                covers=[log] if log else [],
            )
            if newly_stalled:
                cx.record("item.stalled", item_id=item.id, after={"agent_id": actor.account_id})
            if failed.get("failures", 0) >= GIVE_UP_AFTER:
                from slate.service.inbox import person_recipients

                cx.record(
                    "item.run_failed",
                    item_id=item.id,
                    after={
                        "agent_id": actor.account_id,
                        **failed,
                        "notify": sorted(person_recipients(session, item.id)),
                    },
                )
        if row is not None:
            session.delete(row)
    liveactivity.live.clear(item.id)  # the run is over: its box is empty for the next one
    session.refresh(item)
    return item


def _failures(session: Session, account_id: str, item: Item) -> int:
    """The account's consecutive `failed` finishes on the item: back to its last other finish,
    or to the last Run now a person pressed (which starts the count over)."""
    count = 0
    for ev in session.scalars(
        select(Event)
        .where(
            Event.item_id == item.id,
            or_(
                and_(Event.type == "item.work_finished", Event.actor_id == account_id),
                Event.type == "item.run_requested",
            ),
        )
        .order_by(Event.id.desc())
        .limit(50)
    ):
        after = ev.after_json if isinstance(ev.after_json, dict) else {}
        if ev.type == "item.run_requested":
            if after.get("reason") == "now":
                break
        elif after.get("outcome") == "failed":
            count += 1
        else:
            break
    return count


def _keep_run_log(
    session: Session, actor: Actor, item_id: str, start: Event | None
) -> CommentRun | None:
    """Keep what the run posted while it worked (`slate.liveactivity`) with the answer it
    leaves: the caller's newest comment on the item since its take. None when the run posted
    no lines or no comment (nothing to hang them on), or that comment already has a log."""
    if start is None:
        return None
    lines = liveactivity.live.run_lines(item_id, actor.account_id)
    if not lines:
        return None
    session.flush()
    answer = session.scalar(
        select(Comment)
        .where(
            Comment.item_id == item_id,
            Comment.author_id == actor.account_id,
            Comment.created_at >= start.at,
        )
        .order_by(Comment.created_at.desc(), Comment.id.desc())
        .limit(1)
    )
    if answer is None or session.get(CommentRun, answer.id) is not None:
        return None
    log = CommentRun(
        comment_id=answer.id,
        item_id=item_id,
        account_id=actor.account_id,
        steps=len(lines),
        lines_json=lines,
        started_at=start.at,
    )
    session.add(log)
    return log


def run_log(session: Session, comment_id: str) -> dict[str, Any]:
    """The run log kept with a comment (`_keep_run_log`): its lines, oldest first, and when
    the run started and ended. `NotFound` when the comment has none, and for a guest when it
    is a workspace agent's (as with that agent's live box)."""
    comment = comments.get_comment(session, comment_id)
    log = session.get(CommentRun, comment.id)
    if log is None or (
        guests.viewer_is_guest(session) and log.account_id in guests.workspace_agent_ids(session)
    ):
        raise NotFound(f"comment {comment_id} has no run log")
    return {
        "comment_id": log.comment_id,
        "account_id": log.account_id,
        "started_at": log.started_at,
        "finished_at": log.finished_at,
        "lines": log.lines_json,
    }


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
    """Clear parking and ask the item's agent assignees to run it now. The
    `item.run_requested` event (`after = {for, reason}`) reaches their streams as a `work` hint.
    For agents whose `wake` is pull or manual, the event also
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
        session.refresh(item)
        if not session.scalar(select(List.id).where(List.id == item.list_id, WORK_LIST)):
            raise Invalid("reference lists are not offered as work")
        _refuse_if_working(session, item, now)
        if session.get(WorkLease, item.id) is not None:
            raise Conflict("execution state is unknown; reconcile the current run before retrying")
        cx.set(item, "parked_until", None, "item.updated")
        cx.set(item, "parked_note", None, "item.updated")
        before = item.run_requested_at
        item.run_requested_at = now
        item.stalled_at = None  # whoever pressed it wants the agent to go on
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
        if lease_state(lease, now) == "expired":
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


def holds(session: Session, actor: Actor, item_id: str, run_id: str | None) -> WorkLease:
    """The caller's live lease on the task, proven by `run_id`; `Conflict` when it holds none.
    Reads only: for what a run sends that is not a change (`slate.liveactivity`)."""
    return _holding(session, actor, get_item(session, item_id), run_id)


def live_activity(session: Session, item_id: str) -> dict[str, Any]:
    """What the agent working on the task now is doing (`slate.liveactivity`): nothing once it
    stops working, and nothing of a workspace agent for a guest."""
    item = get_item(session, item_id)
    access.check_object(session, item)
    if item.agent_state != AgentState.working or not item.agent_state_by:
        return {"by": None, "lines": []}
    hidden = guests.workspace_agent_ids(session) if guests.viewer_is_guest(session) else ()
    if item.agent_state_by in hidden:
        return {"by": None, "lines": []}
    return liveactivity.live.lines(item.id, item.agent_state_by)


def held_by(session: Session, account_id: str) -> list[dict[str, Any]]:
    """The account's leases, soonest to expire first: live ones and quarantined ones (expired,
    waiting for `reconcile` or a person's Clear). `run_id` is never shown."""
    rows = session.scalars(
        select(WorkLease).where(WorkLease.account_id == account_id).order_by(WorkLease.until)
    )
    return [{**lease_view(r), "run_id": None} for r in rows]


def expire_leases(session: Session, now: datetime) -> int:
    """Scheduler hook: quarantine every lease that ran out. Elapsed time does not prove the
    remote run stopped, so the task stays held and its assignees are unchanged; a `working`
    badge turns `blocked` (an `item.reported` by `system`, `reason` `lease_expired`,
    `reconciliation_required`). The agent's `reconcile` with its run proof, or a person's Clear
    on the badge, settles it."""
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
        if fresh is None or fresh.until >= now:
            return 0
        row = fresh
        item = session.get(Item, row.item_id, populate_existing=True)
        if item is None:
            return 0
        if _ended(session, item, row) == "closed":
            # Also a lease quarantined while the card was open and closed since: nobody can
            # Clear a badge the close already took off, so it would hold the card for ever.
            _settle_closed(session, cx, item, row)
            return 1
        if row.quarantined:
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


def _settle_closed(session: Session, cx: Any, item: Item, row: WorkLease) -> None:
    """A lease that ran out on a closed card is settled, not quarantined. The quarantine keeps a
    card from being run twice at once; a closed card is not run again, every heartbeat since the
    close told the holder to stop, and a "confirm the runtime stopped before retrying" badge on
    a finished card asks a person for nothing they can act on. Records `item.work_finished`
    (reconciled, `runtime_state` `unknown`), clears the holder's flag and drops the lease."""
    start = _turn_start(session, row.account_id, item.id)
    if item.agent_state_by == row.account_id:
        reports.clear_flag(cx, item)
    cx.record(
        "item.work_finished",
        item_id=item.id,
        after={
            "through": start.id if start else None,
            "outcome": "release",
            "reconciled": True,
            "expired": True,
            "runtime_state": "unknown",
            "evidence": "The card was closed and the lease ran out",
            "run_digest": _digest(row.run_id) if row.run_id else None,
        },
        covers=[item],
    )
    session.delete(row)


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
# A chat mention of the agent (service/turns.py): a `work` hint with channel and thread.
CHAT_WAKE_TYPE = "message.mentioned_agent"
# A doc mention of the agent (service/turns.py): a `work` hint with doc_id and thread_id.
DOC_WAKE_TYPE = "doc.mentioned_agent"
# The end of a chat or doc turn: a person's Clear that leaves a mention waiting is a hint too.
CHAT_TURN_TYPE = "message.agent_turn"
DOC_TURN_TYPE = "doc.agent_turn"


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
