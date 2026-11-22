"""The notifications inbox: what happened that an account should hear about, read from `events`.

Nothing is copied into a notifications table. An entry is an event, chosen for one account by
`reason_for()`:

- `mention`  — an explicit @mention, or a task Ask addressed to the account (no watch needed)
- `reply`    — a reply in a chat thread, task discussion, or doc thread the account joined
- `assigned` — the account was added to an item's assignees
- `waiting`  — an item was marked waiting on the account, or an agent asked the account
  again (`item.reported` with `after.waiting_on` and `after.again`: the first time, the
  `waiting_on_account_id` change is the entry). An ask's own Ask (`comment.assigned` with
  `after.ask`) is not also a `mention`.
- `failed`   — Slate gave up running a card after its agent's run failed three times in a row
  (`item.run_failed`, `after.notify`: the card's person assignees and watchers, else the
  owners). A `failed` report before that is nobody's news, and neither is the take that
  retries it.
- `stalled`  — a card's agent stopped without finishing twice (it answered, Slate ran it
  again, it answered again: `item.stalled`). Told to the card's enabled person assignees and
  watchers who can see it now, else the workspace owners (`stall_recipients`).
- `due`      — a due or overdue nudge on an item assigned to the account
- `unblocked` — an open item assigned to the account no longer waits on anything
- `nudge`    — a task is due for (or Run now was pressed on) an agent no runner wakes (`wake`
  pull or manual): its owner is told to go and start it (`item.run_requested` `after.notify`)
- `reminder` — a reminder set for the account came due (service/reminders.py)
- `digest`   — the account's daily digest (no item)
- `health`   — a background poller kept failing, or works again (`health.alert` /
  `health.recovered`, service/health.py); owners and admins only, no item
- `access`   — a guest's browser session ends in three days (`session.expiring`,
  service/sessions.py): told to the guest and to whoever invited them, no item
- `comment`, `status`, `update`, `archived`, `moved` — something changed on an item the
  account follows (assigned to it or watching it)
- `watching` — someone added the account as a watcher by hand (or removed it: a watch
  never ends silently), or any other activity on an item it watches (description, tags,
  assignees, checklists, files, links, subtasks, an agent's report, ...): ClickUp's
  "anything on a task you watch". Creating, being assigned to
  or commenting on an item auto-watches it for people (service/watchers.py), so this is how
  the creator hears about their task. A run of description saves by one author on one item
  shows as its newest entry only.

The account's own changes are never news. Read and cleared state lives in `inbox_state` (a
read-through / cleared-through cursor) and `inbox_marks` (one entry at a time); changing it is
an `inbox.read` / `inbox.cleared` event whose `subject_id` is the account, so every board the
account has open updates its unread count live.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.ids import event_id_floor
from slate.models import (
    Account,
    AccountKind,
    Event,
    InboxMark,
    InboxState,
    Item,
    ItemAssignee,
    Role,
    Watcher,
    WorkLease,
    utcnow,
)
from slate.service import attention
from slate.service.core import Actor, apply_change, event_payload
from slate.service.errors import Invalid

WINDOW = timedelta(days=30)
MAX_ENTRIES = 500
# Candidates read per query; the inbox pages on until it has its entries or the window ends.
PAGE = 500

MENTION_TYPES = (
    "item.commented",
    "item.mentioned",
    "message.posted",
    "message.edited",
    "doc.mentioned",
)
# Chat entries: read with their message (service/chat.py `read_check`).
CHAT_TYPES = ("message.posted", "message.edited")
DUE_TYPES = ("item.due", "item.overdue")
# Told to the item's assignees only (like due nudges), not to watchers.
ASSIGNEE_TYPES = (*DUE_TYPES, "item.unblocked")
HEALTH_TYPES = ("health.alert", "health.recovered")
FOLLOW_TYPES = (
    "item.commented",
    "item.status_changed",
    "item.updated",
    "item.archived",
    "item.trashed",
    "item.moved",
)
# item.updated fields worth a notification; the rest (position, body edits mid-typing) are noise,
# and completed_at always comes with an item.status_changed.
NEWS_FIELDS = frozenset({"due_at", "priority", "title", "start_at"})
# Activity a watcher hears about on top of FOLLOW_TYPES (reason `watching`).
WATCH_TYPES = (
    "item.watched",
    "item.assigned",
    "item.unarchived",
    "item.restored",
    "item.split",
    "item.linked",
    "item.unlinked",
    "item.attachment_added",
    "item.attachment_removed",
    "item.reported",
    "checklist.created",
    "checklist.deleted",
    "checklist.entry_added",
    "checklist.entry_checked",
    "checklist.entry_unchecked",
    "checklist.entry_removed",
)
# item.updated fields that are bookkeeping, never activity (completed_at rides with a status).
QUIET_FIELDS = frozenset({"position", "completed_at", "due_date_only"})


@dataclass(frozen=True)
class Follows:
    """The items an account hears about: assigned (also due nudges) or watching. Creating an
    item does not follow it: what you filed yourself is not news to you."""

    assigned: frozenset[str]
    followed: frozenset[str]
    watched: frozenset[str] = frozenset()
    # Cards whose stall (`item.stalled`, in the window) this account is told about.
    stalls: frozenset[str] = frozenset()


def follows(session: Session, account_id: str) -> Follows:
    assigned = frozenset(
        session.scalars(select(ItemAssignee.item_id).where(ItemAssignee.account_id == account_id))
    )
    watching = frozenset(
        session.scalars(select(Watcher.item_id).where(Watcher.account_id == account_id))
    )
    stalled = session.scalars(
        select(Event.item_id)
        .where(Event.type == "item.stalled", Event.at >= utcnow() - WINDOW)
        .distinct()
    ).all()
    stalls = frozenset(i for i in stalled if account_id in stall_recipients(session, i))
    return Follows(assigned=assigned, followed=assigned | watching, watched=watching, stalls=stalls)


def stall_recipients(session: Session, item_id: str) -> set[str]:
    """Who hears that a card stalled: its enabled person assignees and watchers who can see it
    now (a revoked share counts for nothing), else the enabled workspace owners."""
    from slate.service import access  # noqa: PLC0415 (access imports this module's peers)

    with access.unrestricted(session):
        item = session.get(Item, item_id)
        if item is None:
            return set()
        people = session.scalars(
            select(Account).where(
                Account.kind == AccountKind.person,
                Account.disabled_at.is_(None),
                or_(
                    Account.id.in_(
                        select(ItemAssignee.account_id).where(ItemAssignee.item_id == item_id)
                    ),
                    Account.id.in_(select(Watcher.account_id).where(Watcher.item_id == item_id)),
                ),
            )
        ).all()
        told = set()
        for person in people:
            reach = access.compute(session, person)
            if reach.lists is None or reach.lists.get(item.list_id, 0) >= access.VIEW:
                told.add(person.id)
        return told or set(
            session.scalars(
                select(Account.id).where(Account.role == Role.owner, Account.disabled_at.is_(None))
            )
        )


def _handles(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("mentions")
    return [str(v) for v in value] if isinstance(value, list) else []


def _sees_health(account: Account) -> bool:
    return Role(account.role or Role.member) in (Role.owner, Role.admin)


def reason_for(ev: Event, account: Account, f: Follows) -> str | None:
    """Why `account` should hear about `ev`, or None."""
    if ev.type == "inbox.escalated":
        return (ev.after_json or {}).get("reason") if ev.subject_id == account.id else None
    if ev.type == "item.reminder":
        # Reminders you set for yourself are news too: that is their point.
        return "reminder" if (ev.after_json or {}).get("account_id") == account.id else None
    if ev.type == "account.digest":
        return "digest" if ev.subject_id == account.id else None
    if ev.type in HEALTH_TYPES:
        return "health" if _sees_health(account) else None
    if ev.type == "session.expiring":
        return "access" if ev.subject_id == account.id else None
    if ev.actor_id == account.id:
        return None
    if ev.type == "item.run_requested":
        return "nudge" if account.id in ((ev.after_json or {}).get("notify") or []) else None
    if ev.type == "item.run_failed":
        return "failed" if account.id in ((ev.after_json or {}).get("notify") or []) else None
    if ev.type == "item.stalled":
        return "stalled" if ev.item_id in f.stalls else None
    if ev.type == "comment.assigned":
        after = ev.after_json or {}
        # An agent's ask is told as `waiting` (its wait), not also as a mention.
        return (
            "mention" if after.get("assignee_id") == account.id and not after.get("ask") else None
        )
    if ev.type in MENTION_TYPES and account.handle in _handles(ev.after_json):
        return "mention"
    if ev.type == "item.commented" and account.id in ((ev.after_json or {}).get("reply_to") or []):
        return "reply"
    if ev.type == "doc.mentioned" and account.id in ((ev.after_json or {}).get("reply_to") or []):
        return "reply"
    if ev.type == "message.posted":
        followers = (ev.after_json or {}).get("thread_followers") or []
        return "reply" if account.id in followers else "chat"
    if ev.type == "item.assigned":
        before = set(_handles(ev.before_json))
        if account.handle in _handles(ev.after_json) and account.handle not in before:
            return "assigned"
    if ev.field == "waiting_on_account_id" and ev.after_json == account.id:
        return "waiting"
    if ev.type == "item.reported":
        after = ev.after_json if isinstance(ev.after_json, dict) else {}
        if after.get("waiting_on") == account.id and after.get("again"):
            return "waiting"
        if "failed" in (after.get("state"), (ev.before_json or {}).get("state")):
            return None  # a failed run and its retry are quiet until Slate gives up
        if after.get("reason") == "stale":
            return None  # Slate clearing a badge nothing was running behind
        # Otherwise only a watcher hears about it (below, a change of state).
    if ev.type in DUE_TYPES:
        if ev.item_id in f.assigned or account.id in ((ev.after_json or {}).get("notify") or []):
            return "due"
        return None
    if ev.type == "item.unblocked":
        still_open = (ev.after_json or {}).get("open", True)
        return "unblocked" if ev.item_id in f.assigned and still_open else None
    if ev.type == "item.watched":
        # Added by someone else, by hand (an auto-watch rides with being assigned: no news).
        added = ev.after_json == account.id and ev.field != "auto"
        return "watching" if added else None
    if ev.type == "item.unwatched":
        # Removed by someone else (your own unwatch is not news: the actor check above).
        return "watching" if ev.before_json == account.id else None
    if ev.type == "item.created":
        # A subtask filed under an item the account watches.
        parent = (ev.after_json or {}).get("parent_id") if isinstance(ev.after_json, dict) else None
        return "watching" if parent and parent in f.watched else None
    if ev.item_id not in f.followed:
        return None
    if ev.type == "item.commented":
        return "comment"
    if ev.type == "item.status_changed":
        return "status"
    if ev.type == "item.updated" and ev.field in NEWS_FIELDS:
        return "update"
    if ev.type in ("item.archived", "item.trashed"):
        return "archived"
    if ev.type == "item.moved":
        return "moved"
    if ev.item_id in f.watched and _watch_news(ev):
        return "watching"
    return None


def _watch_news(ev: Event) -> bool:
    """Activity a watcher hears about beyond the follow reasons above."""
    if ev.type == "item.updated":
        return ev.field not in QUIET_FIELDS
    if ev.type == "item.reported":
        # A state change (working, blocked, review, done), not every progress tick.
        before = ev.before_json if isinstance(ev.before_json, dict) else {}
        after = ev.after_json if isinstance(ev.after_json, dict) else {}
        return before.get("state") != after.get("state")
    return ev.type in WATCH_TYPES and ev.type != "item.watched"


# Reasons about a followed item. When the same actor's change on that item also made it wait
# on the account (an agent's blocked reply: a comment, a report and the waiting_on change),
# the `waiting` entry says it all and these are dropped (`waiting_covers`).
FOLLOW_REASONS = frozenset({"comment", "status", "update", "archived", "moved", "watching"})
# Every reason `reason_for()` gives (the module docstring says what each means). The API's
# `Reason` repeats it; `tests/test_state_vocabularies.py` keeps the three in step.
REASONS = (
    "mention",
    "chat",
    "reply",
    "assigned",
    "waiting",
    "due",
    "unblocked",
    "nudge",
    "reminder",
    "digest",
    "health",
    "access",
    "failed",
    "stalled",
    *sorted(FOLLOW_REASONS),
)
SAME_CHANGE = timedelta(seconds=2)


def waits(
    session: Session, account_id: str, item_ids: Iterable[str] | None = None
) -> list[tuple[str | None, str | None, Any]]:
    """(actor, item, at) of recent changes that made an item wait on the account."""
    q = select(Event.actor_id, Event.item_id, Event.at).where(
        Event.at >= utcnow() - WINDOW,
        or_(
            (Event.field == "waiting_on_account_id")
            & (func.json_extract(Event.after_json, "$") == account_id),
            (Event.type == "item.reported")
            & (func.json_extract(Event.after_json, "$.waiting_on") == account_id),
        ),
    )
    if item_ids is not None:
        q = q.where(Event.item_id.in_(list(item_ids)))
    return [tuple(r) for r in session.execute(q)]


def waiting_covers(ev: Event, why: str | None, waited: Sequence[tuple]) -> bool:
    """Whether `ev`'s follow entry is part of a change already told as `waiting`."""
    if why not in FOLLOW_REASONS:
        return False
    return any(
        actor == ev.actor_id and item == ev.item_id and abs(at - ev.at) <= SAME_CHANGE
        for actor, item, at in waited
    )


def notification_turn(
    session: Session, ev: Event, reason: str, now: datetime
) -> tuple[str | None, bool]:
    """The work-start event grouping a card notification, and whether its lease is still live."""
    if not ev.item_id or reason == "waiting":
        return None, False
    boundary = session.scalar(
        select(Event)
        .where(
            Event.item_id == ev.item_id,
            Event.id <= ev.id,
            Event.type.in_(("item.work_started", "item.work_finished")),
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    if ev.type == "item.reported" and (ev.after_json or {}).get("state") == "working":
        # A take records its working badge just before the work-start marker.
        start = session.scalar(
            select(Event)
            .where(
                Event.item_id == ev.item_id,
                Event.actor_id == ev.actor_id,
                Event.type == "item.work_started",
                Event.id > ev.id,
                Event.at <= ev.at + SAME_CHANGE,
            )
            .order_by(Event.id)
            .limit(1)
        )
        if start is not None:
            boundary = start
    if boundary is None or boundary.type != "item.work_started" or boundary.actor_id != ev.actor_id:
        return None, False
    actor = session.get(Account, ev.actor_id) if ev.actor_id else None
    if actor is None or actor.kind != "agent":
        return None, False
    lease = session.get(WorkLease, ev.item_id)
    live = (
        lease is not None
        and lease.account_id == ev.actor_id
        and lease.claimed_at <= ev.at
        and lease.until >= now
    )
    return boundary.id, live


def _state(session: Session, account_id: str) -> InboxState | None:
    return session.get(InboxState, account_id)


def entries(
    session: Session,
    account: Account,
    *,
    limit: int | None = 100,
    unread_only: bool = False,
    reason: str | None = None,
    now: datetime | None = None,
    for_climb: bool = False,
) -> list[dict[str, Any]]:
    """The account's inbox, newest first, each entry the event plus why and whether it is read."""
    f = follows(session, account.id)
    state = _state(session, account.id)
    marks = {
        m.event_id: m.cleared
        for m in session.scalars(select(InboxMark).where(InboxMark.account_id == account.id))
    }
    read_through = state.read_through if state else None
    now = now or utcnow()
    q = _candidates(
        account,
        f,
        state,
        unread_only=unread_only,
        now=now,
        include_chat=for_climb or reason == "chat",
    )
    if for_climb:
        q = q.where(
            or_(
                Event.type.in_(MENTION_TYPES),
                Event.field == "waiting_on_account_id",
                Event.type == "item.reported",
            )
        )
    out: list[dict[str, Any]] = []
    edits: set[tuple[str | None, str]] = set()
    waited = waits(session, account.id)
    chat_read: Callable[[str | None, str], bool] | None = None
    want = min(limit, MAX_ENTRIES) if limit is not None else float("inf")
    before: str | None = None
    while len(out) < want:
        page = session.scalars(q if before is None else q.where(Event.id < before)).all()
        for ev in page:
            why = reason_for(ev, account, f)
            if why is None or marks.get(ev.id) is True or waiting_covers(ev, why, waited):
                continue
            if why == "watching" and ev.field == "body_md":
                # Description autosaves: one entry per author and item, the newest.
                if (ev.item_id, ev.actor_id) in edits:
                    continue
                edits.add((ev.item_id, ev.actor_id))
            if reason and why != reason:
                continue
            read = ev.id in marks or (read_through is not None and ev.id <= read_through)
            if not read and ev.type in CHAT_TYPES and ev.subject_id:
                # A chat mention or reply is read when its message is: the channel's read
                # state is the one record, whichever device or path read it.
                if chat_read is None:
                    from slate.service.chat import read_check  # noqa: PLC0415

                    chat_read = read_check(session, account)
                read = chat_read((ev.after_json or {}).get("channel"), ev.subject_id)
            display = ev
            if ev.type == "inbox.escalated":
                display = attention.original_event(session, ev, account)
                if display is None:
                    continue
                read = read or not attention.pending(session, display, account, why)
            if unread_only and read:
                continue
            view = event_payload(ev)
            if display is not ev:
                view["item_id"] = display.item_id
            level = attention.event_level(session, ev, why)
            out.append(
                {"event": view, "reason": why, "read": read, "loudness": level, "_display": display}
            )
            if len(out) >= want:
                break
        if len(page) < PAGE:
            break
        before = page[-1].id
    _attach_items(session, out)
    return out


def entry_view(session: Session, account: Account, ev: Event, reason: str) -> dict | None:
    """Shape an alert's source entry in the same format as the inbox."""
    display = attention.original_event(session, ev, account) if ev.type == "inbox.escalated" else ev
    if display is None:
        return None
    view = event_payload(ev)
    if display is not ev:
        view["item_id"] = display.item_id
    state = _state(session, account.id)
    mark = session.get(InboxMark, (account.id, ev.id))
    read = bool(
        mark
        or (state and state.read_through and ev.id <= state.read_through)
        or (display is not ev and not attention.pending(session, display, account, reason))
    )
    if not read and ev.type in CHAT_TYPES and ev.subject_id:
        from slate.service.chat import read_check

        read = read_check(session, account)((ev.after_json or {}).get("channel"), ev.subject_id)
    out = {
        "event": view,
        "reason": reason,
        "read": read,
        "loudness": attention.event_level(session, ev, reason),
        "_display": display,
    }
    _attach_items(session, [out])
    return out


def _in_after(path: str, value: str) -> Any:
    """SQL: the JSON array at `path` in the event's `after` holds `value`."""
    each = func.json_each(Event.after_json, path).table_valued("value")
    return select(each.c.value).where(each.c.value == value).exists()


def _candidates(
    account: Account,
    f: Follows,
    state: InboxState | None,
    *,
    unread_only: bool,
    now: datetime,
    include_chat: bool,
):
    """The events `reason_for` could say yes to for this account, newest first, one page.

    Every condition names the account (its handle, its id, an item it follows), so other
    people's traffic never fills the page; `reason_for` stays the judge. The window is also a
    primary-key range (event ids are time-ordered), so the scan stops at the window's start.
    """
    since = now - WINDOW
    me = account.id

    def after(path: str) -> Any:
        return func.json_extract(Event.after_json, path)

    conds = [
        (Event.type == "item.reminder") & (after("$.account_id") == me),
        (Event.type == "account.digest") & (Event.subject_id == me),
        (Event.type == "session.expiring") & (Event.subject_id == me),
        Event.type.in_(MENTION_TYPES) & _in_after("$.mentions", account.handle),
        Event.type.in_(MENTION_TYPES) & _in_after("$", account.handle),
        Event.type.in_(("item.commented", "doc.mentioned")) & _in_after("$.reply_to", me),
        (Event.type == "message.posted") & _in_after("$.thread_followers", me),
        (Event.type == "inbox.escalated") & (Event.subject_id == me),
        Event.type.in_(DUE_TYPES) & _in_after("$.notify", me),
        (Event.type == "item.assigned") & _in_after("$", account.handle),
        (Event.type == "comment.assigned") & (after("$.assignee_id") == me),
        (Event.field == "waiting_on_account_id") & (after("$") == me),
        (Event.type == "item.run_requested") & _in_after("$.notify", me),
        (Event.type == "item.run_failed") & _in_after("$.notify", me),
        (Event.type == "item.reported") & (after("$.waiting_on") == me),
        (Event.type == "item.watched") & Event.field.is_(None) & (after("$") == me),
        (Event.type == "item.unwatched") & (func.json_extract(Event.before_json, "$") == me),
    ]
    if include_chat:
        conds.append(Event.type == "message.posted")
    if _sees_health(account):
        conds.append(Event.type.in_(HEALTH_TYPES))
    if f.stalls:
        conds.append((Event.type == "item.stalled") & Event.item_id.in_(f.stalls))
    if f.followed:
        conds.append(Event.item_id.in_(f.followed) & Event.type.in_(FOLLOW_TYPES + ASSIGNEE_TYPES))
    if f.watched:
        conds.append(Event.item_id.in_(f.watched) & Event.type.in_(WATCH_TYPES))
        conds.append((Event.type == "item.created") & after("$.parent_id").in_(f.watched))
    from slate.service.comments import not_about_deleted_comment  # noqa: PLC0415

    # A day's slack on the id bound: `at` stays the exact test.
    floor = event_id_floor(since - timedelta(days=1))
    if state and state.cleared_through:
        floor = max(floor, state.cleared_through)
    if unread_only and state and state.read_through:
        # Read through the cursor is never unread; marks past it are checked in Python.
        floor = max(floor, state.read_through)
    return (
        select(Event)
        .where(
            Event.id > floor,
            Event.at >= since,
            Event.actor_id != me,
            not_about_deleted_comment(),
            or_(*conds),
        )
        .order_by(Event.id.desc())
        .limit(PAGE)
    )


def _attach_items(session: Session, out: list[dict[str, Any]]) -> None:
    ids = {e["event"]["item_id"] for e in out if e["event"]["item_id"]}
    rows = {i.id: i for i in session.scalars(select(Item).where(Item.id.in_(ids)))} if ids else {}
    for e in out:
        display = e.pop("_display")
        item = rows.get(e["event"]["item_id"] or "")
        e["item_title"] = item.title if item else None
        e["list_id"] = item.list_id if item else None
        e["channel"] = None
        e["doc_id"] = None
        if e["event"]["type"] == "session.expiring":
            after = e["event"]["after"] or {}
            e["item_title"] = f"Access to {after.get('section') or 'the project'}"
        if display.type == "doc.mentioned":
            # A doc page: the entry is about the page (its title as it was then).
            after = display.after_json or {}
            e["doc_id"] = after.get("doc_id")
            e["item_title"] = after.get("title") or "A doc"
        if display.type.startswith("message."):
            # A chat message: the entry is about its channel, not an item.
            after = display.after_json or {}
            e["channel"] = after.get("channel")
            from slate.service.chat import place  # noqa: PLC0415 (chat imports this module's peers)

            e["item_title"] = place(after.get("channel"), after.get("channel_name"))
        if e["event"]["type"] == "inbox.escalated":
            from slate.service.push import notification_for

            wording = notification_for(session, display, e["reason"])
            if wording:
                e["item_title"] = f"Still waiting: {wording['title']}"


def unread_count(session: Session, account: Account) -> int:
    return len(entries(session, account, limit=MAX_ENTRIES, unread_only=True))


def _known(session: Session, event_ids: Iterable[str]) -> list[str]:
    ids = sorted(set(event_ids))
    if not ids:
        return []
    found = set(session.scalars(select(Event.id).where(Event.id.in_(ids))))
    missing = [i for i in ids if i not in found]
    if missing:
        raise Invalid(f"unknown event ids: {', '.join(missing[:5])}")
    return ids


def _item_event_ids(session: Session, account: Account, item_id: str) -> list[str]:
    return [
        e["event"]["id"]
        for e in entries(session, account, limit=MAX_ENTRIES)
        if e["event"]["item_id"] == item_id
    ]


def _newest(session: Session) -> str | None:
    return session.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))


def mark(
    session: Session,
    actor: Actor,
    *,
    cleared: bool,
    event_ids: Sequence[str] = (),
    item_id: str | None = None,
    everything: bool = False,
) -> dict[str, Any]:
    """Mark entries read (or clear them): by event id, every entry for one item, or all.

    One `inbox.read` / `inbox.cleared` event records what was marked.
    """
    account = session.get(Account, actor.account_id)
    assert account is not None
    if not (event_ids or item_id or everything):
        raise Invalid("name event_ids, an item_id, or all")
    if len(event_ids) > MAX_ENTRIES:
        raise Invalid(f"name at most {MAX_ENTRIES} event_ids at once")
    kind = "inbox.cleared" if cleared else "inbox.read"
    with apply_change(session, actor) as cx:
        if everything:
            through = _newest(session)
            state = _state(session, account.id)
            if state is None:
                state = InboxState(account_id=account.id)
                session.add(state)
            state.read_through = through
            if cleared:
                state.cleared_through = through
            cx.record(kind, subject_id=account.id, after={"through": through}, covers=[state])
            return {"through": through}
        ids = list(event_ids) if event_ids else _item_event_ids(session, account, item_id or "")
        ids = _known(session, ids)
        # One query for the marks already there: a `session.get` per id would autoflush the
        # marks added so far before their event is recorded, which the write guard refuses.
        existing = {
            m.event_id: m
            for m in session.scalars(
                select(InboxMark).where(
                    InboxMark.account_id == account.id, InboxMark.event_id.in_(ids)
                )
            )
        }
        touched = []
        for eid in dict.fromkeys(ids):
            m = existing.get(eid)
            if m is None:
                m = InboxMark(account_id=account.id, event_id=eid, cleared=cleared)
                session.add(m)
                touched.append(m)
            elif cleared and not m.cleared:
                m.cleared = True
                touched.append(m)
        if touched:
            cx.record(
                kind,
                subject_id=account.id,
                after={"event_ids": [m.event_id for m in touched]},
                covers=touched,
            )
        return {"event_ids": [m.event_id for m in touched]}
