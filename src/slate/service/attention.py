"""Notification loudness, quiet hours and the scheduler's one-time climb."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.dates import LOCAL
from slate.ids import new_event_id, seed_event_clock
from slate.models import (
    SYSTEM_HANDLE,
    Account,
    AccountKind,
    AttentionAlert,
    Comment,
    Event,
    Item,
    Priority,
)
from slate.service import access, cursors
from slate.service.core import apply_change, event_payload, jsonable

PANEL_ONLY = frozenset({"no_next_step", "decide"})
BASE = {
    "waiting": (3, 3),
    "mention": (3, 2),
    "chat": (3, 1),
    "reply": (2, 1),
    "comment": (2, 1),
    "assigned": (2, 1),
    "reminder": (3, 3),
    "due": (2, 2),
    "failed": (2, 2),
    "stalled": (2, 2),
    "unblocked": (2, 2),
    "nudge": (2, 2),
    "access": (2, 2),
    "health": (3, 3),
    "digest": (1, 1),
}
CLIMB_REASONS = ("waiting", "chat", "mention")
DEFAULT_ESCALATE_MINUTES = 120
ALERT_WINDOW = timedelta(days=1)
_RELEASE_LOCK = threading.Lock()


def loudness(reason: str, actor_kind: str | None, *, urgent: bool = False) -> int:
    """Level shared by all notification channels."""
    if reason in PANEL_ONLY:
        return 0
    return min(4, BASE.get(reason, (0, 0))[actor_kind != AccountKind.person] + urgent)


def event_level(session: Session, ev: Event, reason: str) -> int:
    if ev.type == "inbox.escalated":
        return 4
    actor = session.get(Account, ev.actor_id) if ev.actor_id else None
    item = session.get(Item, ev.item_id) if ev.item_id else None
    return loudness(
        reason,
        actor.kind if actor else None,
        urgent=bool(item and item.priority == Priority.urgent),
    )


def quiet_until(account: Account, at: datetime) -> datetime | None:
    """The UTC end of the workspace-local quiet period containing this instant."""
    quiet = (account.prefs_json or {}).get("quiet_hours")
    if not quiet:
        return None
    begin, end = time.fromisoformat(quiet["from"]), time.fromisoformat(quiet["to"])
    local = at.astimezone(LOCAL)
    wall = local.time()
    held = begin <= wall < end if begin < end else begin != end and (wall >= begin or wall < end)
    if not held:
        return None
    day = local.date() + timedelta(days=begin > end and wall >= begin)
    return datetime.combine(day, end, LOCAL).replace(fold=1).astimezone(UTC)


def open_questions(session: Session, account_id: str, now: datetime) -> list[dict[str, Any]]:
    """Open task and document questions visible to the recipient, oldest first."""
    from slate.models import Doc, DocComment, List, Section, Status, StatusKind
    from slate.service import today
    from slate.service.lists import WORK_LIST

    lead_days = today.lead_days_for(session.get(Account, account_id))
    tasks = session.execute(
        select(Comment, Item)
        .join(Item, Item.id == Comment.item_id)
        .join(List, List.id == Item.list_id)
        .join(Section, Section.id == List.section_id)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(
            Comment.assignee_id == account_id,
            Comment.resolved_at.is_(None),
            Item.archived_at.is_(None),
            Item.completed_at.is_(None),
            Item.parked_until.is_(None),
            List.archived_at.is_(None),
            WORK_LIST,
            Section.archived_at.is_(None),
            (Status.id.is_(None)) | Status.kind.not_in((StatusKind.done, StatusKind.cancelled)),
        )
    )
    docs = session.execute(
        select(DocComment, Doc)
        .join(Doc, Doc.id == DocComment.doc_id)
        .where(
            DocComment.assignee_id == account_id,
            DocComment.resolved_at.is_(None),
            Doc.archived_at.is_(None),
        )
    )
    ordered = sorted(
        [(ask, obj) for ask, obj in tasks if not today.is_later(obj, now=now, lead_days=lead_days)]
        + list(docs),
        key=lambda row: (row[0].created_at, row[0].id),
    )
    return [
        {
            "id": obj.id,
            "comment_id": ask.id,
            "title": obj.title,
            "question": ((ask.ask_json or {}).get("question") or ask.body_md)
            if isinstance(ask, Comment)
            else ask.body_md,
            "at": jsonable(ask.created_at),
            "age_minutes": max(0, int((now - ask.created_at).total_seconds() // 60)),
            **({"doc_id": obj.id} if isinstance(obj, Doc) else {}),
        }
        for ask, obj in ordered
    ]


def pending(session: Session, ev: Event, account: Account, reason: str) -> bool:
    """Whether the original entry still warrants a raised alert."""
    from slate.models import Message
    from slate.service import email

    if reason == "waiting":
        item = session.get(Item, ev.item_id) if ev.item_id else None
        return bool(
            item
            and item.waiting_on_account_id == account.id
            and any(q["id"] == item.id for q in open_questions(session, account.id, ev.at))
        )
    if ev.type.startswith("message."):
        live = session.scalar(
            select(Message.id).where(Message.id == ev.subject_id, Message.deleted_at.is_(None))
        )
        if live is None:
            return False
    return not email.seen(session, account, ev)


def original_event(session: Session, ev: Event, account: Account) -> Event | None:
    """Recheck visibility of the referenced event, even when the climb itself is visible."""
    original = session.scalar(
        select(Event).where(Event.id == (ev.after_json or {}).get("event_id"))
    )
    if original is None:
        return None
    gate = access.EventGate(account.id)
    gate.load(session)
    return original if gate.project(session, event_payload(original)) is not None else None


def escalate(session: Session, now: datetime) -> int:
    """Record a single recipient event for each overdue open question or unseen person entry."""
    from slate.service import inbox
    from slate.service.due import system_actor

    release(session, now)
    sent = 0
    people = session.scalars(select(Account).where(Account.disabled_at.is_(None))).all()
    for account in people:
        minutes = (account.prefs_json or {}).get("escalate_after_minutes", DEFAULT_ESCALATE_MINUTES)
        if not minutes or account.handle == SYSTEM_HANDLE:
            continue
        if minutes > (now - datetime.min.replace(tzinfo=UTC)).total_seconds() / 60:
            continue
        cutoff = now - timedelta(minutes=minutes)
        gate = access.EventGate(account.id)
        gate.load(session)
        with access.reading_as(session, account):
            questions = {q["id"]: q for q in open_questions(session, account.id, now)}
            entries = inbox.entries(session, account, limit=None, now=now, for_climb=True)
            candidates = []
            waiting = set()
            for entry in entries:
                reason, view = entry["reason"], entry["event"]
                if reason not in CLIMB_REASONS or view["type"] == "inbox.escalated":
                    continue
                ev = session.get(Event, view["id"])
                if reason == "waiting":
                    if ev.item_id in waiting:
                        continue
                    waiting.add(ev.item_id)
                    item = session.get(Item, ev.item_id) if ev.item_id else None
                    if (
                        ev.item_id not in questions
                        or not item
                        or item.waiting_on_account_id != account.id
                    ):
                        continue
                    age = questions[ev.item_id]["age_minutes"]
                    if age < minutes:
                        continue
                elif (
                    entry["read"]
                    or not pending(session, ev, account, reason)
                    or (author := session.get(Account, ev.actor_id)) is None
                    or author.kind != AccountKind.person
                ):
                    continue
                if ev.at <= cutoff and gate.project(session, event_payload(ev)) is not None:
                    candidates.append((ev, reason, int((now - ev.at).total_seconds() // 60)))
        if not candidates:
            continue
        with apply_change(session, system_actor(session)) as cx:
            already = set(
                session.scalars(
                    select(func.json_extract(Event.after_json, "$.event_id")).where(
                        Event.type == "inbox.escalated", Event.subject_id == account.id
                    )
                )
            )
            for ev, reason, age in candidates:
                with access.reading_as(session, account):
                    still_pending = ev.id not in already and pending(session, ev, account, reason)
                if still_pending:
                    cx.record(
                        "inbox.escalated",
                        subject_id=account.id,
                        after={"event_id": ev.id, "reason": reason, "minutes": age},
                    )
                    sent += 1
    if sent:
        release(session, now)
    return sent


def release(session: Session, now: datetime) -> None:
    """Collect new account entries and release the eligible ones once for all channels."""
    from slate.service import inbox

    with _RELEASE_LOCK:
        session.expire_all()
        accounts = {
            a.id: a
            for a in session.scalars(
                select(Account).where(
                    Account.disabled_at.is_(None),
                    Account.handle != SYSTEM_HANDLE,
                )
            )
        }
        gates = {a: access.EventGate(a) for a in accounts}
        for gate in gates.values():
            gate.load(session)
        follows = {a: inbox.follows(session, a) for a in accounts}

        def handle(ev: Event):
            for aid, account in accounts.items():
                why = inbox.reason_for(ev, account, follows[aid])
                if (
                    not why
                    or not event_level(session, ev, why)
                    or gates[aid].project(session, event_payload(ev)) is None
                ):
                    continue
                if why in inbox.FOLLOW_REASONS and inbox.waiting_covers(
                    ev, why, inbox.waits(session, aid, [ev.item_id or ""])
                ):
                    continue
                if session.get(AttentionAlert, (aid, ev.id)) is None:
                    yield AttentionAlert(account_id=aid, event_id=ev.id, source_at=ev.at)

        cursors.follow(
            session,
            "push",
            now=now,
            window=ALERT_WINDOW,
            handle=handle,
            follows=follows,
            refresh=lambda aid: inbox.follows(session, aid),
        )
        clock = cursors.engine_cursor(session, "alert_release")
        pending_rows = session.scalars(
            select(AttentionAlert)
            .where(AttentionAlert.cursor.is_(None))
            .order_by(AttentionAlert.event_id)
        ).all()
        groups: dict[tuple, list[tuple[AttentionAlert, str, int]]] = {}
        for row in pending_rows:
            account = accounts.get(row.account_id)
            ev = session.get(Event, row.event_id)
            why = inbox.reason_for(ev, account, follows[account.id]) if account and ev else None
            level = event_level(session, ev, why) if why else 0
            if (
                not level
                or now >= row.source_at + ALERT_WINDOW
                or gates[row.account_id].project(session, event_payload(ev)) is None
            ):
                session.delete(row)
                continue
            turn, live = (
                inbox.notification_turn(session, ev, why, now)
                if account.kind == AccountKind.person
                else (None, False)
            )
            if live:
                continue
            quiet = None
            if level < 4:
                quiet = quiet_until(account, now) or quiet_until(account, ev.at)
            if quiet and quiet > now:
                continue
            group = quiet or turn or ev.id
            groups.setdefault((account.id, group), []).append((row, why, level))
        if groups:
            seed_event_clock((clock.event_id,))
            for (_, group), batch in groups.items():
                batch.sort(key=lambda x: (x[2], x[0].event_id), reverse=True)
                winner, why, level = batch[0]
                winner.cursor = clock.event_id = new_event_id()
                winner.at, winner.reason, winner.loudness = now, why, level
                if isinstance(group, datetime):
                    winner.quiet_events = [
                        {"id": row.event_id, "reason": reason} for row, reason, _ in batch
                    ]
                for row, _, _ in batch[1:]:
                    session.delete(row)
            clock.updated_at = now
        for row in session.scalars(
            select(AttentionAlert).where(AttentionAlert.source_at < now - inbox.WINDOW).limit(500)
        ):
            session.delete(row)
        session.commit()


def released(
    session: Session,
    account: Account,
    *,
    after: str = "0",
    limit: int | None = None,
    event_id: str | None = None,
    since: datetime | None = None,
    token_id: str | None = None,
    render: bool = True,
) -> list[dict[str, Any]]:
    """Read the same ordered, access-checked releases for every transport."""
    from slate.service import inbox, push

    q = (
        select(AttentionAlert)
        .where(AttentionAlert.account_id == account.id, AttentionAlert.cursor > after)
        .order_by(AttentionAlert.cursor)
    )
    if event_id:
        q = q.where(AttentionAlert.event_id == event_id)
    if since:
        q = q.where(AttentionAlert.at >= since)
    rows = session.scalars(q).all()
    if not render:
        return [
            {
                "event_id": row.event_id,
                "reason": row.reason,
                "loudness": row.loudness,
                "released_at": row.at,
                "source_at": row.source_at,
            }
            for row in rows
        ]
    if not rows:
        return []
    gate = access.EventGate(account.id, token_id)
    gate.load(session)
    out = []
    with access.reading_as(session, gate.access or account):
        for row in rows:
            notes = []
            for ref in row.quiet_events or [{"id": row.event_id, "reason": row.reason}]:
                ev = session.scalar(select(Event).where(Event.id == ref["id"]))
                if ev is None:
                    continue
                payload = event_payload(ev)
                projected = gate.project(session, payload)
                if projected is None or (account.role == "guest" and projected != payload):
                    continue
                note = push.notification_for(session, ev, ref["reason"])
                if note:
                    notes.append((ev, note, projected))
            if not notes:
                continue
            ev, note, projected = notes[0]
            entry = None
            if row.quiet_events:
                note = {
                    **note,
                    "title": f"{len(notes)} things while you were away",
                    "body": "; ".join(n["title"] for _, n, _ in notes)[:240],
                    "tag": f"quiet:{row.cursor}",
                }
            elif token_id:
                entry = inbox.entry_view(session, account, ev, row.reason)
                if entry:
                    entry["event"] = {**projected, "item_id": entry["event"]["item_id"]}
            item_id = entry["event"]["item_id"] if entry else ev.item_id
            out.append(
                {
                    **note,
                    "cursor": row.cursor,
                    "at": jsonable(row.at),
                    "event_id": row.event_id,
                    "loudness": row.loudness,
                    "silent": row.loudness < 3,
                    "reason": row.reason,
                    "item_id": None if row.quiet_events else item_id,
                    "entry": entry,
                }
            )
            if limit is not None and len(out) >= limit:
                break
    return out


def latest_cursor(session: Session, account_id: str) -> str:
    """The starting point for a new alert reader."""
    return (
        session.scalar(
            select(AttentionAlert.cursor)
            .where(AttentionAlert.account_id == account_id, AttentionAlert.cursor.is_not(None))
            .order_by(AttentionAlert.cursor.desc())
            .limit(1)
        )
        or "0"
    )
