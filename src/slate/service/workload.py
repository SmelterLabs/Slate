"""Capacity and the Box view's numbers: what each person and agent can take on, and how much
open work they have against it.

Capacity is set per account (`capacities`, `set_capacity`): hours a day, tasks a day and the
weekdays worked. An account with no row has `DEFAULTS` for its kind. `boxes` counts open,
in-progress, overdue and recently finished tasks per assignee, with open minutes against this
week's capacity. The board computes the same numbers from its live items (web/src/lib/box.ts);
this module is what REST and MCP answer, so an agent sees what the board shows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.dates import LOCAL, local_today
from slate.models import (
    Account,
    AccountKind,
    Capacity,
    ItemAssignee,
    Status,
    StatusKind,
)
from slate.service import access
from slate.service.core import Actor, apply_change
from slate.service.due import SYSTEM_HANDLE
from slate.service.errors import Forbidden, Invalid, NotFound

MAX_DAYS = 92

DEFAULTS: dict[str, dict[str, Any]] = {
    "person": {
        "hours_per_day": 8.0,
        "tasks_per_day": None,
        "work_days": [0, 1, 2, 3, 4],
    },
    "agent": {
        "hours_per_day": 8.0,
        "tasks_per_day": None,
        "work_days": [0, 1, 2, 3, 4, 5, 6],
    },
}
FIELDS = ("hours_per_day", "tasks_per_day", "work_days")


# --- capacity -------------------------------------------------------------------------------


def _days_of(text: str) -> list[int]:
    return sorted({int(x) for x in text.split(",") if x.strip() != ""})


def capacity_view(account: Account, row: Capacity | None) -> dict[str, Any]:
    """An account's capacity settings, its kind's defaults when it has none (`custom` false)."""
    base = dict(DEFAULTS[account.kind.value])
    if row is not None:
        base = {
            "hours_per_day": row.hours_per_day,
            "tasks_per_day": row.tasks_per_day,
            "work_days": _days_of(row.work_days),
        }
    return {"account_id": account.id, "custom": row is not None, **base}


def _rows(session: Session, account_ids: Iterable[str] | None = None) -> dict[str, Capacity]:
    q = select(Capacity)
    if account_ids is not None:
        q = q.where(Capacity.account_id.in_(list(account_ids)))
    return {c.account_id: c for c in session.scalars(q)}


def roster(session: Session, kinds: str = "all") -> list[Account]:
    """Who the Box view shows: enabled people and agents (not the system account),
    people first, then by name."""
    if kinds not in ("all", "people", "agents"):
        raise Invalid("kinds must be all, people or agents")
    q = select(Account).where(Account.disabled_at.is_(None), Account.handle != SYSTEM_HANDLE)
    if kinds == "people":
        q = q.where(Account.kind == AccountKind.person)
    elif kinds == "agents":
        q = q.where(Account.kind == AccountKind.agent)
    accounts = list(session.scalars(q))
    visible = access.participant_ids(session)  # a guest's roster names no workspace agent
    if visible is not None:
        accounts = [a for a in accounts if a.id in visible]
    accounts.sort(key=lambda a: (a.kind != AccountKind.person, a.display_name.lower()))
    return accounts


def list_capacities(session: Session, kinds: str = "all") -> list[dict[str, Any]]:
    """Every shown account's capacity (defaults included)."""
    accounts = roster(session, kinds)
    rows = _rows(session, [a.id for a in accounts])
    return [capacity_view(a, rows.get(a.id)) for a in accounts]


def get_capacity(session: Session, account_id: str) -> dict[str, Any]:
    account = session.get(Account, account_id)
    if account is None:
        raise NotFound(f"account {account_id} not found")
    return capacity_view(account, _rows(session, [account_id]).get(account_id))


def _number(value: Any, name: str, top: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= top:
        raise Invalid(f"{name} is a number from 0 to {top:g}, or null for no limit")
    return float(value)


def _capacity_values(changes: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(changes) - set(FIELDS)
    if unknown:
        raise Invalid(f"unknown capacity fields: {sorted(unknown)}; use {list(FIELDS)}")
    out: dict[str, Any] = {}
    if "hours_per_day" in changes:
        out["hours_per_day"] = _number(changes["hours_per_day"], "hours_per_day", 24)
    if "tasks_per_day" in changes:
        n = _number(changes["tasks_per_day"], "tasks_per_day", 1000)
        if n is not None and n != int(n):
            raise Invalid("tasks_per_day is a whole number")
        out["tasks_per_day"] = None if n is None else int(n)
    if "work_days" in changes:
        days = changes["work_days"]
        if not isinstance(days, list | tuple) or not all(
            isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days
        ):
            raise Invalid("work_days is a list of weekdays, 0 = Monday to 6 = Sunday")
        out["work_days"] = ",".join(str(d) for d in sorted(set(days)))
    return out


def _may_set(session: Session, actor: Actor, account: Account) -> None:
    if account.handle == SYSTEM_HANDLE:
        raise Forbidden("the system account has no capacity")
    if actor.account_id == account.id:
        return
    by = session.get(Account, actor.account_id)
    if by is None or not access.is_admin(session, by.id):
        raise Forbidden("only an owner or admin can set someone else's capacity")


def set_capacity(
    session: Session, actor: Actor, account_id: str, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Set some of an account's capacity fields (`capacity.set`, one event per field). An
    account sets its own; an owner or admin sets anyone's."""
    account = session.get(Account, account_id)
    if account is None:
        raise NotFound(f"account {account_id} not found")
    _may_set(session, actor, account)
    values = _capacity_values(changes)
    row = _rows(session, [account_id]).get(account_id)
    with apply_change(session, actor) as cx:
        if row is None:
            base = DEFAULTS[account.kind.value]
            row = Capacity(
                account_id=account_id,
                hours_per_day=base["hours_per_day"],
                tasks_per_day=base["tasks_per_day"],
                work_days=",".join(str(d) for d in base["work_days"]),
            )
            for key, value in values.items():
                setattr(row, key, value)
            cx.create(row, "capacity.set")
        else:
            for key, value in values.items():
                cx.set(row, key, value, "capacity.set")
    return capacity_view(account, row)


def reset_capacity(session: Session, actor: Actor, account_id: str) -> dict[str, Any]:
    """Back to the defaults for the account's kind (`capacity.reset`)."""
    account = session.get(Account, account_id)
    if account is None:
        raise NotFound(f"account {account_id} not found")
    _may_set(session, actor, account)
    row = _rows(session, [account_id]).get(account_id)
    if row is not None:
        with apply_change(session, actor) as cx:
            cx.delete(row, "capacity.reset")
    return capacity_view(account, None)


def day_capacity(cap: Mapping[str, Any], day: date, metric: str) -> float | None:
    """What an account can take on in one day, in the metric's unit (None = no limit)."""
    works = day.weekday() in cap["work_days"]
    if metric == "estimate":
        hours = cap["hours_per_day"]
        return None if hours is None else (hours * 60 if works else 0.0)
    tasks = cap["tasks_per_day"]
    return None if tasks is None else (float(tasks) if works else 0.0)


# --- boxes ----------------------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def boxes(
    session: Session,
    *,
    kinds: str = "all",
    section_id: str | None = None,
    list_id: str | None = None,
    done_days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    """ClickUp's Box view in numbers: per person and agent (and one box for nobody), how many
    open tasks they have by status kind, how many are overdue (due before now, as cards
    show), how many they finished in the last `done_days` days, and their open load in
    minutes against this week's capacity (Monday to Sunday). Tasks only;
    cancelled and trashed ones never count."""
    from slate.service import items as items_svc

    if not 1 <= done_days <= MAX_DAYS:
        raise Invalid(f"done_days is 1 to {MAX_DAYS}")
    now = now or datetime.now(LOCAL)
    today = local_today(now)
    since = now - timedelta(days=done_days)
    monday = today - timedelta(days=today.weekday())
    week = [monday + timedelta(days=n) for n in range(7)]
    accounts = roster(session, kinds)
    caps = {a.id: capacity_view(a, row) for a, row in _with_rows(session, accounts)}
    rows = [
        i
        for i in items_svc.query_items(session, section_id=section_id, list_id=list_id, kind="task")
        if i.trashed_at is None
    ]
    kind_of = {sid: k for sid, k in session.execute(select(Status.id, Status.kind))}
    owners: dict[str, list[str]] = {}
    if rows:
        for item_id, account_id in session.execute(
            select(ItemAssignee.item_id, ItemAssignee.account_id).where(
                ItemAssignee.item_id.in_([i.id for i in rows])
            )
        ):
            owners.setdefault(item_id, []).append(account_id)

    def blank() -> dict[str, Any]:
        return {"open": 0, "active": 0, "overdue": 0, "done": 0, "minutes": 0}

    tally: dict[str | None, dict[str, Any]] = {a.id: blank() for a in accounts}
    tally[None] = blank()
    for item in rows:
        kind = kind_of.get(item.status_id, StatusKind.open)
        if kind == StatusKind.cancelled:
            continue
        for who in owners.get(item.id) or [None]:
            box = tally.get(who)
            if box is None:
                continue  # someone outside the roster (disabled, or filtered out by kinds)
            if kind == StatusKind.done:
                done_at = item.completed_at
                if done_at is not None and _aware(done_at) >= since and item.archived_at is None:
                    box["done"] += 1
                continue
            if item.archived_at is not None:
                continue
            box["active" if kind == StatusKind.active else "open"] += 1
            if item.due_at is not None and _aware(item.due_at) < now:
                box["overdue"] += 1
            box["minutes"] += item.estimate_minutes or 0

    def shaped(box: dict[str, Any], cap: Mapping[str, Any] | None) -> dict[str, Any]:
        minutes = None if cap is None else _bucket_capacity(cap, week, "estimate")
        box["capacity_minutes"] = minutes
        box["over"] = minutes is not None and box["minutes"] > minutes
        return box

    out = [
        {
            "account": {
                "id": a.id,
                "handle": a.handle,
                "display_name": a.display_name,
                "kind": a.kind.value,
            },
            **shaped(tally[a.id], caps[a.id]),
        }
        for a in accounts
    ]
    out.append({"account": None, **shaped(tally[None], None)})
    return {
        "today": today.isoformat(),
        "done_days": done_days,
        "week_start": monday.isoformat(),
        "boxes": out,
    }


def _with_rows(session: Session, accounts: list[Account]) -> list[tuple[Account, Capacity | None]]:
    rows = _rows(session, [a.id for a in accounts])
    return [(a, rows.get(a.id)) for a in accounts]


def _bucket_capacity(cap: Mapping[str, Any], days: list[date], metric: str) -> float | None:
    total = 0.0
    for d in days:
        c = day_capacity(cap, d, metric)
        if c is None:
            return None
        total += c
    return round(total, 2)
