"""Reminders and the daily digest: Slate telling someone about something at a time.

Reminders (`reminders` table): one account, one item, either a fixed `remind_at` or
`before_minutes` before the item's due date. Setting one records `item.reminder_set`, removing
one `item.reminder_removed`. The scheduler hook `fire_reminders()` records `item.reminder`
(actor: the `slate` system agent; `after` names the account, the note and the due date) when
one comes due, for open items only (not done or cancelled, not archived). Each reminder fires
once per time it points at (`fired_for`), so a reminder before a due date fires again if the
date moves later and the time comes round again.

Alerts before the due date (the board's "Before due" toggles): any number per item, none by
default. Setting the same `before_minutes` for the same account twice returns the one already
there (no second alert, no event), so toggles and agent retries are safe. A date-only due date
counts back from its due time, 17:00 workspace time (when it turns overdue): "1 hour before"
is 16:00 that day, "1 day before" 17:00 the day before. A new instance of a recurring task gets
the previous instance's before-due alerts (`copy_due_alerts`); fixed-time reminders stay put.

The daily digest (`send_digests()`, also a scheduler hook): at the account's `digest_hour`
(workspace time; prefs `digest` and `digest_hour`, default on at 7 for people, off for agents)
one `account.digest` event per account per day, with what is overdue, due today and waiting on
it. Nothing is recorded on a day with nothing in it. Idempotent: the day is in the event.

Both kinds of event reach the account's inbox (reasons `reminder`, `digest`), its check-in
(`why` the same), webhooks filtered with `assignee_me`, and phone/desktop push (slate.pusher).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from slate.dates import LOCAL, local_day_bounds, parse_when
from slate.ids import new_id
from slate.models import (
    Account,
    AccountKind,
    Event,
    Item,
    List,
    Reminder,
    Section,
    Status,
    StatusKind,
    utcnow,
)
from slate.service import access
from slate.service import items as items_svc
from slate.service import today as today_svc
from slate.service.core import Actor, apply_change, jsonable
from slate.service.errors import Forbidden, Invalid, NotFound

MAX_PER_ITEM = 20
MAX_BEFORE_MINUTES = 60 * 24 * 60  # 60 days
DEFAULT_DIGEST_HOUR = 7
DIGEST_ITEMS = 10  # per group, in the event
# An account whose digest came out empty is not checked again for this long (the same day):
# building a digest costs an access computation and three queries, every scheduler tick.
QUIET_RECHECK = timedelta(hours=1)
# account id -> (local date, when it was last found empty). Process-local; a restart checks
# everyone once more, which is harmless (an empty digest records nothing).
_quiet: dict[str, tuple[str, datetime]] = {}


# --- Reads ---------------------------------------------------------------------------------


def effective_at(reminder: Reminder, due_at: datetime | None) -> datetime | None:
    """When the reminder goes off now: its fixed time, or due minus the lead (None if undated)."""
    if reminder.remind_at is not None:
        return reminder.remind_at
    if reminder.before_minutes is None or due_at is None:
        return None
    return due_at - timedelta(minutes=reminder.before_minutes)


def reminder_view(session: Session, r: Reminder, item: Item | None = None) -> dict[str, Any]:
    item = item or session.get(Item, r.item_id)
    at = effective_at(r, item.due_at if item else None)
    account = session.get(Account, r.account_id)
    return {
        "id": r.id,
        "item_id": r.item_id,
        "item_title": item.title if item else None,
        "account_id": r.account_id,
        "account": account.handle if account else None,
        "remind_at": r.remind_at,
        "before_minutes": r.before_minutes,
        "at": at,
        "note": r.note,
        "fired": at is not None and r.fired_for == at,
        "created_by": r.created_by,
        "created_at": r.created_at,
    }


def reminders_of(session: Session, item_id: str) -> list[dict[str, Any]]:
    item = items_svc.get_item(session, item_id)
    rows = session.scalars(
        select(Reminder).where(Reminder.item_id == item_id).order_by(Reminder.created_at)
    )
    return [reminder_view(session, r, item) for r in rows]


def upcoming(session: Session, account_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """The account's reminders that have not fired yet, soonest first (undated ones last)."""
    rows = session.execute(
        select(Reminder, Item)
        .join(Item, Item.id == Reminder.item_id)
        .where(Reminder.account_id == account_id, Item.archived_at.is_(None))
    ).all()
    views = [reminder_view(session, r, i) for r, i in rows]
    views = [v for v in views if not v["fired"]]
    far = datetime.max.replace(tzinfo=LOCAL)
    views.sort(key=lambda v: v["at"] or far)
    return views[:limit]


# --- Writes --------------------------------------------------------------------------------


def add_reminder(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    at: str | datetime | None = None,
    before_minutes: int | None = None,
    account: str | None = None,
    note: str | None = None,
) -> Reminder:
    """Remind `account` (handle or id; default the caller) about the item, `at` a time
    (ISO 8601 or YYYY-MM-DD) or `before_minutes` before its due date (0 = at the due time).
    Reminding someone else needs edit on the item's list."""
    item = items_svc.get_item(session, item_id)
    if (at is None) == (before_minutes is None):
        raise Invalid("give `at` (a time) or `before_minutes` (before the due date), not both")
    remind_at = parse_when(at) if at is not None else None
    if before_minutes is not None:
        if isinstance(before_minutes, bool) or not 0 <= int(before_minutes) <= MAX_BEFORE_MINUTES:
            raise Invalid(f"before_minutes must be between 0 and {MAX_BEFORE_MINUTES}")
        if item.due_at is None:
            raise Invalid("this item has no due date; remind at a time instead")
    who = items_svc.resolve_account(session, account) if account else None
    account_id = who.id if who else actor.account_id
    if account_id != actor.account_id:
        access.require_list_level(
            session, item.list_id, access.EDIT, "set a reminder for someone else"
        )
    if before_minutes is not None:
        same = session.scalar(
            select(Reminder).where(
                Reminder.item_id == item.id,
                Reminder.account_id == account_id,
                Reminder.before_minutes == int(before_minutes),
            )
        )
        if same is not None:
            return same
    if note is not None and len(note) > 280:
        raise Invalid("note is limited to 280 characters")
    count = len(list(session.scalars(select(Reminder.id).where(Reminder.item_id == item.id))))
    if count >= MAX_PER_ITEM:
        raise Invalid(f"an item keeps at most {MAX_PER_ITEM} reminders")
    reminder = Reminder(
        id=new_id(),
        item_id=item.id,
        account_id=account_id,
        remind_at=remind_at,
        before_minutes=int(before_minutes) if before_minutes is not None else None,
        note=(note or "").strip() or None,
        created_by=actor.account_id,
    )
    with apply_change(session, actor) as cx:
        cx.create(reminder, "item.reminder_set")
    return reminder


def remove_reminder(session: Session, actor: Actor, reminder_id: str) -> None:
    """Its target or its creator may remove a reminder."""
    r = session.get(Reminder, reminder_id)
    if r is None:
        raise NotFound(f"reminder {reminder_id} not found")
    if actor.account_id not in (r.account_id, r.created_by):
        raise Forbidden("only the reminded account or whoever set it can remove a reminder")
    with apply_change(session, actor) as cx:
        cx.delete(r, "item.reminder_removed")


def copy_due_alerts(cx: Any, source: Item, target: Item) -> None:
    """The source's before-due alerts onto the target (a recurring task's next instance), one
    `item.reminder_set` each. Fixed-time reminders belong to their moment and are not copied."""
    rows = cx.session.scalars(
        select(Reminder)
        .where(Reminder.item_id == source.id, Reminder.before_minutes.is_not(None))
        .order_by(Reminder.created_at)
    ).all()
    for r in rows[:MAX_PER_ITEM]:
        cx.create(
            Reminder(
                id=new_id(),
                item_id=target.id,
                account_id=r.account_id,
                before_minutes=r.before_minutes,
                note=r.note,
                created_by=r.created_by,
            ),
            "item.reminder_set",
        )


def _open_items_filter():
    return (
        Item.archived_at.is_(None),
        List.archived_at.is_(None),
        Section.archived_at.is_(None),
        or_(Status.id.is_(None), Status.kind.not_in([StatusKind.done, StatusKind.cancelled])),
    )


def fire_reminders(session: Session, *, now: datetime | None = None) -> int:
    """Scheduler hook: record `item.reminder` for every reminder whose time has come."""
    from slate.service.due import system_actor

    now = now or utcnow()
    rows = session.execute(
        select(Reminder, Item)
        .join(Item, Item.id == Reminder.item_id)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(*_open_items_filter())
    ).all()
    fired = 0
    actor: Actor | None = None
    for r, item in rows:
        at = effective_at(r, item.due_at)
        if at is None or at > now or r.fired_for == at:
            continue
        actor = actor or system_actor(session)
        handle = session.scalar(select(Account.handle).where(Account.id == r.account_id))
        with apply_change(session, actor) as cx:
            r.fired_for = at
            cx.record(
                "item.reminder",
                item_id=item.id,
                after={
                    "reminder_id": r.id,
                    "account_id": r.account_id,
                    "account": handle,
                    "note": r.note,
                    "at": jsonable(at),
                    "due_at": jsonable(item.due_at),
                },
                covers=[r],
            )
        fired += 1
    return fired


# --- Daily digest --------------------------------------------------------------------------


def digest_settings(account: Account) -> tuple[bool, int]:
    prefs = account.prefs_json or {}
    on = prefs.get("digest")
    if on is None:
        on = account.kind == AccountKind.person
    hour = prefs.get("digest_hour")
    return bool(on), int(hour) if isinstance(hour, int) else DEFAULT_DIGEST_HOUR


def _brief(items: list[Item]) -> list[dict[str, Any]]:
    return [
        {
            "id": i.id,
            "title": i.title,
            "due_at": jsonable(i.due_at),
            "priority": jsonable(i.priority),
        }
        for i in items[:DIGEST_ITEMS]
    ]


def digest_for(
    session: Session, account: Account, *, now: datetime | None = None
) -> dict[str, Any]:
    """What the account's digest says right now (also `GET /me/digest`)."""
    now = now or utcnow()
    from slate.service import agents as agents_svc  # agents reads work, which reads items

    caller = access.current(session)
    # Scheduler sessions are unrestricted. An assignment is not an access grant.
    with access.unrestricted(session):
        recipient = access.compute(session, account)
    with access.unrestricted(session):
        access.attach(session, caller or recipient)
        try:
            assigned = set(items_svc.query_items(session, assignee=account.id))
            day = today_svc.today(session, account.id, now=now)
            # Agents today: what each agent runs by the end of the day (the plan's rule).
            _, day_end = local_day_bounds(now)
            crews = agents_svc.plan(session, now=now, until=day_end)
        finally:
            session.info.pop(access.ACCESS_KEY, None)
    # Today's view is everyone's; a digest is about what is yours.
    mine = {k: [i for i in day[k] if i in assigned] for k in ("overdue", "today")}
    body = {
        "date": now.astimezone(LOCAL).date().isoformat(),
        "counts": {
            "overdue": len(mine["overdue"]),
            "today": len(mine["today"]),
            "waiting": len(day["waiting"]),
        },
        "overdue": _brief(mine["overdue"]),
        "today": _brief(mine["today"]),
        "waiting": _brief(day["waiting"]),
    }
    agents_today = [
        {
            "id": a["id"],
            "handle": a["handle"],
            "display_name": a["display_name"],
            "next": [
                {
                    "item_id": n["item_id"],
                    "title": n["title"],
                    "list": n["list"],
                    "reason": n["reason"],
                    "at": jsonable(n["at"]),
                }
                for n in a["next"][:DIGEST_ITEMS]
            ],
        }
        for a in crews
        if a["next"]
    ]
    if agents_today:
        body["agents"] = agents_today
    return body


def _sent_on(session: Session, account_id: str, date: str) -> bool:
    last = session.scalar(
        select(Event)
        .where(Event.type == "account.digest", Event.subject_id == account_id)
        .order_by(Event.id.desc())
        .limit(1)
    )
    return last is not None and (last.after_json or {}).get("date") == date


def send_digests(session: Session, *, now: datetime | None = None) -> int:
    """Scheduler hook: one `account.digest` per account per day, at its digest hour."""
    from slate.service.due import SYSTEM_HANDLE, system_actor

    now = now or utcnow()
    local = now.astimezone(LOCAL)
    sent = 0
    accounts = session.scalars(
        select(Account).where(Account.disabled_at.is_(None), Account.handle != SYSTEM_HANDLE)
    ).all()
    for account in accounts:
        on, hour = digest_settings(account)
        if not on or local.hour < hour:
            continue
        date = local.date().isoformat()
        quiet = _quiet.get(account.id)
        if quiet and quiet[0] == date and now - quiet[1] < QUIET_RECHECK:
            continue
        if _sent_on(session, account.id, date):
            continue
        body = digest_for(session, account, now=now)
        if not any(body["counts"].values()) and not body.get("agents"):
            _quiet[account.id] = (date, now)
            continue
        _quiet.pop(account.id, None)
        with apply_change(session, system_actor(session)) as cx:
            cx.record("account.digest", subject_id=account.id, after=body)
        sent += 1
    return sent
