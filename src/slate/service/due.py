"""Due events: `item.due_soon`, `item.due`, `item.overdue`, emitted by the scheduler.

Per item the stages, in time order, are:

    item.due_soon  at due_at - L, for each distinct lead L among active subscriptions that want
                   due_soon (filter `due_lead_hours`, default 24; just 24 when there are none)
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

Actor: the `slate` system agent (source `system`), created on first need. The event's `field`
is `due_at`; `after` is `{"due_at": ..., "lead_hours": L}` for due_soon, `{"due_at": ...}`
otherwise. The column write uses a Core UPDATE so `items.updated_at` is not touched.
"""

from __future__ import annotations

from collections.abc import Iterable
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
    Item,
    List,
    Section,
    Source,
    Status,
    StatusKind,
    Webhook,
)
from slate.service import webhooks
from slate.service.core import Actor, apply_change, in_change, jsonable

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


def active_leads(session: Session) -> set[int]:
    hooks = session.scalars(select(Webhook).where(Webhook.disabled_at.is_(None)))
    leads = {webhooks.lead_hours(h) for h in hooks if webhooks.wants_type(h, "item.due_soon")}
    return leads or {webhooks.DEFAULT_LEAD_HOURS}


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
    leads: Iterable[int],
    now: datetime,
    agents: list[str] | None = None,
    date_only: bool = False,
) -> list[Stage]:
    """The stages to emit now, oldest first. `agents`: the item's agent assignees (a run stage
    when there are any). `date_only`: the item's `due_date_only` (its run stage is 06:00)."""
    due_iso = jsonable(due_at)
    stages = [
        Stage(
            due_at - timedelta(hours=h),
            f"soon:{h}",
            "item.due_soon",
            {"due_at": due_iso, "lead_hours": h},
        )
        for h in set(leads)
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
    leads = active_leads(session)
    candidates = list(
        session.scalars(
            select(Item)
            .join(List, List.id == Item.list_id)
            .join(Section, Section.id == List.section_id)
            .outerjoin(Status, Status.id == Item.status_id)
            .where(
                Item.due_at.is_not(None),
                Item.due_at <= now + max(timedelta(hours=max(leads)), RUN_LEAD),
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
            leads,
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
                    cx.record(stage.type, item_id=item.id, field="due_at", after=stage.after)
        set_committed_value(item, "last_due_event", todo[-1].key)
        emitted += len(todo)
    return emitted
