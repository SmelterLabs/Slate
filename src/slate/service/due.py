"""Due events: `item.due_soon`, `item.due`, `item.overdue`, emitted by the scheduler.

Per item the stages, in time order, are:

    item.due_soon  at due_at - 24 h
    item.run_requested  at dates.run_at(...) (06:00 workspace time for a date-only due date,
                   else due_at), only when an agent is assigned: the agent's runner wakes and
                   the due gate in service/work.py opens. Actor `slate`, `after.reason` = `due`.
    item.due       at due_at
    item.overdue   at due_at + 1 h

`items.last_due_event` holds the key of the last stage emitted (`soon:<hours>`, `due`,
`overdue`). A tick emits every stage whose time has come and that lies after the stored one,
so a restart, a repeated tick or a missed hour never repeats or skips a stage. `due_soon` is
dropped once `due_at` itself has passed (no "due soon" for something already due).

If `due_at` moves so that the stored stage now lies in the future, the item starts over for
the new date. Archived items (or items in archived lists/sections) and items in a done or
cancelled status emit nothing.

Retries (`retry_failed_runs`, on the same tick): a card whose agent's run ended `failed` is
offered to that agent again at its retry time (service/work.py `RETRY_AFTER`, from the
failed finish), by the same event: `item.run_requested` with `after.reason` = `retry`, which
also sets `run_requested_at` (Run now's mark) so the work loop hands the card out. Once per
failure: a card already asked again since its badge is not asked twice.

Actor: the `slate` system agent (source `system`), created on first need. The event's `field`
is `due_at`; `after` is `{"due_at": ..., "lead_hours": 24}` for due_soon, `{"due_at": ...}`
otherwise. The column write uses a Core UPDATE so `items.updated_at` is not touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from slate import dates
from slate.ids import new_id
from slate.models import (
    SYSTEM_HANDLE,
    Account,
    AccountKind,
    AgentState,
    Event,
    Item,
    ItemAssignee,
    List,
    Role,
    Section,
    Source,
    Status,
    StatusKind,
    WorkLease,
)
from slate.service.core import Actor, apply_change, in_change, jsonable

DUE_SOON_HOURS = 24
OVERDUE_AFTER = timedelta(hours=1)


@dataclass(frozen=True)
class Stage:
    at: datetime
    key: str
    type: str
    after: dict[str, Any]


SYSTEM_NAME = "Slate"


def system_actor(session: Session) -> Actor:
    """The `slate` agent account (created on first need, as its own actor) as a system actor.

    It is Slate's own account, not an agent anyone runs: if it was renamed or disabled before
    `update_account` refused that, it is put back here (recorded as its own events)."""
    account = session.scalar(select(Account).where(Account.handle == SYSTEM_HANDLE))
    if account is None:
        account = Account(
            id=new_id(), handle=SYSTEM_HANDLE, display_name=SYSTEM_NAME, kind=AccountKind.agent
        )
        with apply_change(session, Actor(account_id=account.id, source=Source.system)) as cx:
            cx.create(account, "account.created")
    elif (account.display_name != SYSTEM_NAME or account.disabled_at is not None) and not in_change(
        session
    ):  # inside another actor's change: the next call repairs it
        with apply_change(session, Actor(account_id=account.id, source=Source.system)) as cx:
            if account.display_name != SYSTEM_NAME:
                cx.set(account, "display_name", SYSTEM_NAME, "account.updated")
            if account.disabled_at is not None:
                cx.set(account, "disabled_at", None, "account.enabled")
    return Actor(account_id=account.id, source=Source.system)


RUN_LEAD = timedelta(hours=11)  # the earliest a run stage comes before due_at (06:00 vs 17:00)


def _threshold(key: str, due_at: datetime, date_only: bool = False) -> datetime | None:
    if key == "due":
        return due_at
    if key == "run":
        return dates.run_at(due_at, date_only) - timedelta(microseconds=1)
    if key == "overdue":
        return due_at + OVERDUE_AFTER
    if key.startswith("soon:") and key[5:].isdigit():
        return due_at - timedelta(hours=int(key[5:]))
    return None


def pending_stages(
    due_at: datetime,
    last: str | None,
    now: datetime,
    agents: list[str] | None = None,
    date_only: bool = False,
) -> list[Stage]:
    """The stages to emit now, oldest first. `agents`: the item's agent assignees (a run stage
    when there are any). `date_only`: the item's `due_date_only` (its run stage is 06:00)."""
    due_iso = jsonable(due_at)
    stages = [
        Stage(
            due_at - timedelta(hours=DUE_SOON_HOURS),
            f"soon:{DUE_SOON_HOURS}",
            "item.due_soon",
            {"due_at": due_iso, "lead_hours": DUE_SOON_HOURS},
        )
    ]
    if agents:
        # Sorted before `due` when both fall at due_at (a due date with a time).
        stages.append(
            Stage(
                dates.run_at(due_at, date_only) - timedelta(microseconds=1),
                "run",
                "item.run_requested",
                {"for": agents, "reason": "due", "due_at": due_iso},
            )
        )
    stages.append(Stage(due_at, "due", "item.due", {"due_at": due_iso}))
    stages.append(Stage(due_at + OVERDUE_AFTER, "overdue", "item.overdue", {"due_at": due_iso}))
    stages.sort(key=lambda s: s.at)

    done_until = _threshold(last, due_at, date_only) if last else None
    if done_until is not None and done_until > now:
        done_until = None  # due_at moved later: a new deadline starts over
    out = [s for s in stages if s.at <= now and (done_until is None or s.at > done_until)]
    if now >= due_at:
        out = [s for s in out if s.type != "item.due_soon"]
    return out


def _ran_since(item: Item, run: Stage) -> bool:
    """Has one of the run stage's agents taken or reported on the item since its run time?"""
    return (
        item.agent_state_by in run.after["for"]
        and item.agent_state_at is not None
        and item.agent_state_at >= run.at
    )


def emit_due_events(session: Session, *, now: datetime) -> int:
    """One scheduler tick. Returns the number of events emitted."""
    candidates = list(
        session.scalars(
            select(Item)
            .join(List, List.id == Item.list_id)
            .join(Section, Section.id == List.section_id)
            .outerjoin(Status, Status.id == Item.status_id)
            .where(
                Item.due_at.is_not(None),
                Item.due_at <= now + max(timedelta(hours=DUE_SOON_HOURS), RUN_LEAD),
                Item.archived_at.is_(None),
                List.archived_at.is_(None),
                Section.archived_at.is_(None),
                or_(
                    Status.id.is_(None),
                    Status.kind.not_in([StatusKind.done, StatusKind.cancelled]),
                ),
                or_(
                    Item.last_due_event.is_(None),
                    Item.last_due_event != "overdue",
                    Item.due_at > now - OVERDUE_AFTER,
                ),
            )
            .order_by(Item.due_at, Item.id)
        )
    )
    actor: Actor | None = None
    emitted = 0
    from slate.service.work import agent_assignees, nudge_fields

    for item in candidates:
        todo = pending_stages(
            item.due_at,
            item.last_due_event,
            now,
            agent_assignees(session, item.id),
            item.due_date_only,
        )
        if not todo:
            continue
        run = next((s for s in todo if s.key == "run"), None)
        if run and _ran_since(item, run):
            # One of its agents already took it after the run time (assigning it woke one):
            # asking again would only add a confusing second request. Nothing is written, so
            # the next stage (due) moves `last_due_event` past the run.
            todo = [s for s in todo if s is not run]
            run = None
            if not todo:
                continue
        actor = actor or system_actor(session)
        nudge = nudge_fields(session, run.after["for"]) if run else {}
        with apply_change(session, actor) as cx:
            session.execute(
                update(Item)
                .where(Item.id == item.id)
                .values(last_due_event=todo[-1].key, updated_at=Item.updated_at)
                .execution_options(synchronize_session=False)
            )
            for stage in todo:
                if stage.key == "run":
                    # The same event as Run now, so one path wakes the agent (service/work.py).
                    after = {**stage.after, **nudge}
                    cx.record(stage.type, item_id=item.id, field="run_requested_at", after=after)
                else:
                    after = stage.after
                    if stage.key in ("due", "overdue") and not session.scalar(
                        select(Account.id)
                        .join(ItemAssignee, ItemAssignee.account_id == Account.id)
                        .where(ItemAssignee.item_id == item.id, Account.kind == AccountKind.person)
                        .limit(1)
                    ):
                        creator = session.get(Account, item.created_by)
                        recipient = (
                            creator.id
                            if creator and creator.kind == AccountKind.person
                            else session.scalar(
                                select(Account.id).where(Account.role == Role.owner)
                            )
                        )
                        if recipient:
                            after = {**after, "notify": [recipient]}
                    cx.record(stage.type, item_id=item.id, field="due_at", after=after)
        set_committed_value(item, "last_due_event", todo[-1].key)
        emitted += len(todo)
    return emitted


def retry_failed_runs(session: Session, *, now: datetime) -> int:
    """Ask the agent of each failed run whose retry time has come to run the card again (module
    docstring). Returns how many were asked."""
    from slate.service.work import RETRY_AFTER, nudge_fields

    failed = session.scalars(
        select(Item)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(
            Item.agent_state == AgentState.failed,
            or_(Item.run_requested_at.is_(None), Item.run_requested_at < Item.agent_state_at),
            Item.archived_at.is_(None),
            List.archived_at.is_(None),
            Section.archived_at.is_(None),
            or_(Status.id.is_(None), Status.kind.not_in([StatusKind.done, StatusKind.cancelled])),
        )
    ).all()
    actor: Actor | None = None
    asked = 0
    for item in failed:
        last = session.scalar(
            select(Event.after_json)
            .where(
                Event.item_id == item.id,
                Event.actor_id == item.agent_state_by,
                Event.type == "item.work_finished",
            )
            .order_by(Event.id.desc())
            .limit(1)
        )
        failures = (last or {}).get("failures") if (last or {}).get("outcome") == "failed" else 0
        if not isinstance(failures, int) or not 1 <= failures <= len(RETRY_AFTER):
            continue  # gave up, or a failed badge no finish set: Run now starts it over
        if item.agent_state_at + RETRY_AFTER[failures - 1] > now:
            continue
        agent = item.agent_state_by
        actor = actor or system_actor(session)
        nudge = nudge_fields(session, [agent])
        with apply_change(session, actor) as cx:
            before = item.run_requested_at
            item.run_requested_at = now
            cx.record(
                "item.run_requested",
                item_id=item.id,
                field="run_requested_at",
                before=before,
                after={"for": [agent], "reason": "retry", **nudge},
                covers=[item],
            )
        asked += 1
    return asked


def clear_stale_badges(session: Session, *, now: datetime) -> int:
    """Clear each `working` badge with no lease that no report refreshed for `STALE_AFTER`:
    nothing is running there. An `idle` report by `slate` (`after.reason` `stale`) that
    reaches nobody's inbox. Each candidate is read again under the write lock, so a take or a
    report that won the gap is left alone. Returns how many were cleared."""
    from slate.service.reports import flag_of
    from slate.service.work import STALE_AFTER

    def stale() -> Any:
        return select(Item.id).where(
            Item.agent_state == AgentState.working,
            Item.agent_state_at < now - STALE_AFTER,
            Item.id.not_in(select(WorkLease.item_id)),
        )

    actor: Actor | None = None
    cleared = 0
    for item_id in session.scalars(stale()).all():
        actor = actor or system_actor(session)
        with apply_change(session, actor) as cx:
            if session.scalar(stale().where(Item.id == item_id)) is None:
                continue
            item = session.get(Item, item_id, populate_existing=True)
            before = flag_of(item)
            item.agent_state = item.agent_state_by = item.agent_state_at = None
            item.agent_note = item.agent_progress = item.agent_url = None
            cx.record(
                "item.reported",
                item_id=item.id,
                field="agent_state",
                before=before,
                after={
                    "state": "idle",
                    "note": None,
                    "progress": None,
                    "by": actor.account_id,
                    "url": None,
                    "reason": "stale",
                },
                covers=[item],
            )
            cleared += 1
    return cleared


def poke_stalled(session: Session, *, now: datetime) -> int:
    """Ask the agent of each stalled card to run it again, once (docs/agents-work-loop.md,
    "Stalled cards"): open, waiting on nobody, no review, blocked or failed badge, not blocked
    by another card, no start or due time still to come, no lease, and an enabled agent
    assignee that may edit it whose newest finish there was `answered` at least `STALL_AFTER`
    ago with no Run now since. Records `item.run_requested` (`after.reason` `stalled`, `for`
    that one agent); the work loop hands the card to that agent once (`work._stall_poke`).
    Each candidate is read again under the write lock. Returns how many were poked."""
    from slate.service.work import STALL_AFTER, agent_assignees, nudge_fields

    def stalled() -> Any:
        return (
            select(Item.id)
            .join(List, List.id == Item.list_id)
            .join(Section, Section.id == List.section_id)
            .outerjoin(Status, Status.id == Item.status_id)
            .where(
                Item.archived_at.is_(None),
                List.archived_at.is_(None),
                Section.archived_at.is_(None),
                or_(
                    Status.id.is_(None),
                    Status.kind.not_in([StatusKind.done, StatusKind.cancelled]),
                ),
                Item.stalled_at.is_(None),
                Item.waiting_on_account_id.is_(None),
                or_(
                    Item.agent_state.is_(None),
                    Item.agent_state.not_in(
                        [AgentState.review, AgentState.blocked, AgentState.failed]
                    ),
                ),
                Item.blocked_since.is_(None),
                or_(Item.start_at.is_(None), Item.start_at <= now),
                or_(Item.due_at.is_(None), Item.due_at <= now),
                Item.id.not_in(select(WorkLease.item_id)),
            )
        )

    def answered_long_ago(item: Item, agent_id: str) -> bool:
        newest = session.scalar(
            select(Event)
            .where(
                Event.item_id == item.id,
                or_(
                    Event.type == "item.run_requested",
                    (Event.type == "item.work_finished") & (Event.actor_id == agent_id),
                ),
            )
            .order_by(Event.id.desc())
            .limit(1)
        )
        return (
            newest is not None
            and newest.type == "item.work_finished"
            and (newest.after_json or {}).get("outcome") == "answered"
            and newest.at <= now - STALL_AFTER
        )

    def can_take(item: Item, agent_id: str) -> bool:
        from slate.service import access

        agent = session.get(Account, agent_id)
        if agent is None or agent.disabled_at is not None:
            return False
        reach = access.compute(session, agent)
        level = access.EDIT if reach.lists is None else reach.lists.get(item.list_id, 0)
        return min(reach.cap, level) >= access.EDIT

    actor: Actor | None = None
    poked = 0
    for item_id in session.scalars(stalled()).all():
        actor = actor or system_actor(session)
        with apply_change(session, actor) as cx:
            if session.scalar(stalled().where(Item.id == item_id)) is None:
                continue
            item = session.get(Item, item_id, populate_existing=True)
            agent = next(
                (
                    a
                    for a in agent_assignees(session, item.id)
                    if can_take(item, a) and answered_long_ago(item, a)
                ),
                None,
            )
            if agent is None:
                continue
            cx.record(
                "item.run_requested",
                item_id=item.id,
                field="run_requested_at",
                after={"for": [agent], "reason": "stalled", **nudge_fields(session, [agent])},
            )
            poked += 1
    return poked
