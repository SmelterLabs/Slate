"""Web Push: the browsers and installed apps that get notifications, and what they are told.

A subscription (`push_subscriptions`) is one browser's endpoint and keys for one account; the
board makes one when someone turns notifications on for that device (`push.subscribed`) and
removes it when they turn them off (`push.unsubscribed`). Its keys are never in an event.

What is pushed is the account's inbox (service/inbox.py `reason_for`), filtered by the
account's prefs `push_reasons` (default `DEFAULT_REASONS`): the same entries, worded for a
lock screen by `notification_for()`. One reason is push-only: `chat`, any new message in a
channel the account can read that is not already a mention or a reply to it (`chat_reason`).
It never lands in the Inbox: a busy channel is not news to keep, but a desktop toast for it is
what makes chat usable from another window. One inbox entry is never pushed: an agent's report
that it started work (`started_work`); the report that ends the run is. `renudge()` follows
chat up: chat that is still
unread `chat_renudge_minutes` (prefs; default 10, 0 = off) after its newest message gets one
more push per newest message ("Still unread: #Todo"), so a toast that slid by is not the end
of it. The engine step runs both (slate.pusher). `process()` is the engine step: read events
after the `engine_cursors` row `push`, commit per-device delivery rows with the cursor (one
commit per page), then send. `push_deliveries` survives restarts, keeps successes terminal and
rechecks access and preferences at every attempt. Transient failures retry at most five times
in 24 hours with bounded backoff/Retry-After; old events are not replayed. Rendering runs under
the recipient's read filter, so an opaque reference never expands to a private task title. A
push service that answers 404 or 410 has forgotten the subscription, so it is removed
(`push.expired`).

Receipts are bookkeeping (`core.BOOKKEEPING_MODELS`), like webhook deliveries: the table is
the delivery log and the retry state, so queueing, sending, failing and expiring a receipt
writes no event.
"""

from __future__ import annotations

import contextlib
import fnmatch
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate import webpush
from slate.ids import new_id
from slate.models import (
    Account,
    EngineCursor,
    Event,
    Item,
    PushDelivery,
    PushSubscription,
    Source,
    utcnow,
)
from slate.service import access, chat, inbox, webhooks
from slate.service.core import Actor, apply_change, event_payload
from slate.service.errors import Forbidden, Invalid, NotFound
from slate.settings import settings

log = logging.getLogger(__name__)

CURSOR = "push"
PAGE = 500
MAX_PER_ACCOUNT = 20
MAX_FAILURES = 10
MAX_ATTEMPTS = 5
DELIVERY_BATCH = 100
DELIVERY_WINDOW = timedelta(days=1)
DEFAULT_REASONS = (
    "mention",
    "reply",
    "chat",
    "assigned",
    "waiting",
    "due",
    "unblocked",
    "nudge",
    "reminder",
    "digest",
    "health",
    "access",
    "watching",
)

RENUDGE_DEFAULT_MINUTES = 10
RENUDGE_MAX_MINUTES = 24 * 60
# The newest unread message is left alone once it is this old: a channel nobody reads for a
# week is not nudged again on every restart.
RENUDGE_WINDOW = timedelta(days=1)

# (endpoint, p256dh, auth, payload) -> (HTTP status or None, error)
Sender = Callable[
    [str, str, str, dict[str, Any]], tuple[int | None, str] | tuple[int | None, str, str | None]
]


# --- Subscriptions -------------------------------------------------------------------------


def _check_keys(p256dh: str, auth: str) -> None:
    try:
        ok = len(webpush.unb64url(p256dh)) == 65 and len(webpush.unb64url(auth)) == 16
    except (ValueError, TypeError):
        ok = False
    if not ok:
        raise Invalid("keys must be the browser's p256dh (65 bytes) and auth (16 bytes), base64url")


def subscription_view(sub: PushSubscription) -> dict[str, Any]:
    return {
        "id": sub.id,
        "label": sub.label,
        "endpoint_host": urlsplit(sub.endpoint).netloc,
        "created_at": sub.created_at,
        "last_ok_at": sub.last_ok_at,
        "failures": sub.failures,
    }


def list_subscriptions(session: Session, account_id: str) -> list[PushSubscription]:
    return list(
        session.scalars(
            select(PushSubscription)
            .where(PushSubscription.account_id == account_id)
            .order_by(PushSubscription.created_at)
        )
    )


def endpoint_allowed(host: str) -> bool:
    """Is `host` one of `settings.push_endpoint_hosts` (glob patterns; "*" means any)? Slate
    POSTs encrypted notifications to the endpoint, so an unknown host would be a way to make
    it call arbitrary https servers."""
    host = host.lower().rstrip(".")
    return any(fnmatch.fnmatchcase(host, p.lower()) for p in settings.push_endpoint_hosts)


def subscribe(
    session: Session,
    actor: Actor,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    label: str | None = None,
) -> PushSubscription:
    """Register this browser for the caller. The same endpoint again updates it in place (a
    browser that signs in as someone else moves to them)."""
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc:
        raise Invalid("endpoint must be the browser's https push URL")
    if not endpoint_allowed(parts.hostname or ""):
        raise Invalid(
            f"{parts.hostname} is not a known push service; an admin can add it with "
            "SLATE_PUSH_ENDPOINT_HOSTS"
        )
    # The allowlist names hosts; the address must still be public (unless private targets
    # are on), so a wildcard allowlist cannot aim Slate at this network. A name that does
    # not resolve now is judged again at send time, which fails closed.
    with contextlib.suppress(webhooks.LookupFailed):
        webhooks.check_target(endpoint)
    _check_keys(p256dh, auth)
    label = (label or "").strip()[:100] or None
    existing = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if existing is None:
        count = session.scalar(
            select(func.count())
            .select_from(PushSubscription)
            .where(PushSubscription.account_id == actor.account_id)
        )
        if count >= MAX_PER_ACCOUNT:
            raise Invalid(f"an account keeps at most {MAX_PER_ACCOUNT} devices for notifications")
    with apply_change(session, actor) as cx:
        if existing is None:
            sub = PushSubscription(
                id=new_id(),
                account_id=actor.account_id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                label=label,
            )
            cx.create(
                sub, "push.subscribed", after={"account_id": actor.account_id, "label": label}
            )
            return sub
        existing.account_id = actor.account_id
        existing.p256dh, existing.auth, existing.label = p256dh, auth, label
        existing.failures = 0
        cx.record(
            "push.subscribed",
            subject_id=existing.id,
            after={"account_id": actor.account_id, "label": label},
            covers=[existing],
        )
        return existing


def unsubscribe(session: Session, actor: Actor, subscription_id: str) -> None:
    sub = session.get(PushSubscription, subscription_id)
    if sub is None:
        raise NotFound(f"push subscription {subscription_id} not found")
    if sub.account_id != actor.account_id:
        raise Forbidden("you can only turn off your own devices")
    with apply_change(session, actor) as cx:
        cx.record(
            "push.unsubscribed",
            subject_id=sub.id,
            before={"account_id": sub.account_id, "label": sub.label},
            covers=[sub],
        )
        session.delete(sub)


def unsubscribe_endpoint(session: Session, actor: Actor, endpoint: str) -> bool:
    sub = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if sub is None or sub.account_id != actor.account_id:
        return False
    unsubscribe(session, actor, sub.id)
    return True


def _expire(session: Session, sub: PushSubscription, why: str) -> None:
    """The push service forgot it: remove it as its own account, through the system door."""
    actor = Actor(account_id=sub.account_id, source=Source.system, session_ref="push")
    with apply_change(session, actor) as cx:
        cx.record(
            "push.expired",
            subject_id=sub.id,
            before={"account_id": sub.account_id, "label": sub.label, "why": why},
            covers=[sub],
        )
        session.delete(sub)


# --- What a notification says --------------------------------------------------------------


def _name(session: Session, account_id: str | None) -> str:
    if not account_id:
        return "Someone"
    acct = session.get(Account, account_id)
    return acct.display_name if acct else "Someone"


def chat_reason(ev: Event, account: Account) -> str | None:
    """`chat` for a new top-level or thread message by someone else. Which channels the account
    can read is the caller's gate (service/access.py `EventGate`), not decided here."""
    if ev.type != "message.posted" or ev.actor_id == account.id:
        return None
    return "chat"


WATCH_VERBS = {
    "item.watched": "added you as a watcher",
    "item.created": "added a subtask",
    "item.assigned": "changed the assignees",
    "item.unarchived": "brought back a task",
    "item.restored": "restored a task from the trash",
    "item.split": "split a task into subtasks",
    "item.linked": "linked a task",
    "item.unlinked": "removed a task link",
    "item.attachment_added": "attached a file",
    "item.attachment_removed": "removed a file",
    "checklist.created": "added a checklist",
    "checklist.deleted": "removed a checklist",
    "checklist.entry_added": "added a checklist item",
    "checklist.entry_checked": "ticked a checklist item",
    "checklist.entry_unchecked": "unticked a checklist item",
    "checklist.entry_removed": "removed a checklist item",
}
FIELD_WORDS = {
    "body_md": "the description",
    "tags": "the tags",
    "estimate_minutes": "the estimate",
    "waiting_on_account_id": "who it waits on",
    "acceptance_md": "the acceptance criteria",
    "kind": "the type",
    "external_ref": "the reference",
}
REPORT_WORDS = {
    "working": "started working",
    "blocked": "is blocked",
    "review": "is ready for review",
    "done": "finished",
    "idle": "stopped working",
}


def watch_verb(ev: Event) -> str:
    """What happened, after the actor's name: "changed the description", "attached a file"."""
    if ev.type == "item.updated":
        field = ev.field or "a field"
        return f"changed {FIELD_WORDS.get(field, field.replace('_', ' '))}"
    if ev.type == "item.reported":
        after = ev.after_json if isinstance(ev.after_json, dict) else {}
        return REPORT_WORDS.get(str(after.get("state")), "reported")
    return WATCH_VERBS.get(ev.type, "changed a task you watch")


def notification_for(session: Session, ev: Event, reason: str) -> dict[str, Any]:
    """Title, body, the link a tap opens, and a tag (one notification per item replaces the
    last on the lock screen)."""
    item = session.get(Item, ev.item_id) if ev.item_id else None
    title = item.title if item else "Slate"
    who = _name(session, ev.actor_id)
    after = ev.after_json if isinstance(ev.after_json, dict) else {}
    url = f"/#/inbox?task={item.id}" if item else "/#/home"
    if ev.type.startswith("message."):
        # A chat message: open its channel at the message (or its thread).
        where = chat.place(after.get("channel"), after.get("channel_name"))
        text = chat.readable(session, after.get("body_md") or "")[:140]
        if not text and after.get("files"):
            text = "📎 " + ", ".join(str(f.get("filename")) for f in after["files"])[:138]
        thread = after.get("parent_id") or after.get("id") or ev.subject_id
        url = f"/#/chat/{after.get('channel') or 'general'}?thread={thread}"
        head = f"{who} replied in {where}" if reason == "reply" else f"{who} mentioned you"
        if reason == "mention":
            text = f"{where}: {text}"
        elif reason == "chat":
            head = f"{who} in {where}"
            if after.get("parent_id"):
                head += " (thread)"
        # One notification per thread; plain channel traffic replaces itself per channel, so a
        # busy channel is one toast, not a stack.
        tag = (
            f"chat:{thread}"
            if reason != "chat" or after.get("parent_id")
            else f"chat:{after.get('channel')}"
        )
        return {
            "title": head[:120],
            "body": text[:240],
            "url": url,
            "tag": tag,
            "event_id": ev.id,
            "reason": reason,
        }
    if ev.type == "doc.mentioned":
        doc_title = after.get("title") or "a doc"
        doc_url = f"/#/docs/{after.get('doc_id') or ev.subject_id}"
        if after.get("comment_id"):
            doc_url += f"?comment={after['comment_id']}"
        verb = (
            "replied"
            if reason == "reply"
            else "asked you"
            if after.get("asked")
            else "mentioned you"
        )
        return {
            "title": f"{who} {verb} in {doc_title}"[:120],
            "body": (after.get("excerpt") or doc_title)[:240],
            "url": doc_url,
            "tag": f"doc:{after.get('doc_id') or ev.subject_id}",
            "event_id": ev.id,
            "reason": reason,
        }
    if reason == "health":
        label = after.get("label") or "A background job"
        if ev.type == "health.alert":
            head = f"{label} is failing"
            body = f"Failed {after.get('failures') or 'several'} runs in a row: "
            body += after.get("error") or "see the Health page"
        else:
            head, body = f"{label} works again", "It recovered on its own."
        return {
            "title": head[:120],
            "body": body[:240],
            "url": "/#/health",
            "tag": f"health:{after.get('check')}",
            "event_id": ev.id,
            "reason": reason,
        }
    if reason == "access":
        # A guest session ends in three days: the guest asks for a link, the inviter sends one.
        section = after.get("section") or "the project"
        date = after.get("date") or "soon"
        where = f"/#/section/{after['section_id']}" if after.get("section_id") else "/#/home"
        if after.get("for") == "inviter":
            name = after.get("account_name") or after.get("account") or "A guest"
            head = f"{name}'s access to {section} ends {date}"
            body = "Extend it or copy a new link from Share & invite before then."
        else:
            inviter = (
                after.get("inviter_name") or after.get("inviter") or "the person who invited you"
            )
            head = f"Your access to {section} ends {date}"
            body = f"Ask {inviter} for a new link before then."
        return {
            "title": head[:120],
            "body": body[:240],
            "url": where,
            "tag": f"access:{after.get('token_id') or ev.id}",
            "event_id": ev.id,
            "reason": reason,
        }
    if reason == "reminder":
        head, body = f"⏰ {title}", after.get("note") or "Reminder"
    elif reason == "digest":
        after = access.digest_snapshot(session, after, access.current(session))
        if not any((after.get("counts") or {}).values()):
            return None
        c = after.get("counts") or {}
        bits = [
            f"{c.get('overdue', 0)} overdue" if c.get("overdue") else "",
            f"{c.get('today', 0)} due today" if c.get("today") else "",
            f"{c.get('waiting', 0)} waiting on you" if c.get("waiting") else "",
        ]
        head = "Your day in Slate"
        body = ", ".join(b for b in bits if b) or "Nothing due"
        firsts = [i["title"] for i in (after.get("overdue") or []) + (after.get("today") or [])]
        if firsts:
            body += ": " + "; ".join(firsts[:3])
        crews = [a for a in (after.get("agents") or []) if isinstance(a, dict)]
        if crews:
            n = sum(len(a.get("next") or []) for a in crews)
            body += f". Agents today: {n} task{'' if n == 1 else 's'}"
    elif ev.type == "comment.assigned":
        head, body = f"{who} asked you", title
    elif reason == "mention":
        head, body = f"{who} mentioned you", f"{title}: {(after.get('body_md') or '')[:140]}"
    elif reason == "assigned":
        head, body = f"{who} assigned you", title
    elif reason == "waiting":
        # An agent's question (its blocked report's note) reads better than the bare title.
        asked = (
            item is not None
            and item.agent_state is not None
            and item.agent_state.value == "blocked"
            and item.agent_state_by == ev.actor_id
        )
        if asked:
            head, body = f"{who} needs you on: {title}", item.agent_note or "Open it to answer"
        else:
            head, body = f"{who} is waiting on you", title
    elif reason == "unblocked":
        head, body = "Ready to start", f"{title}: nothing it waits on is open any more"
    elif reason == "nudge":
        names = [n.get("name") or "An agent" for n in after.get("nudge") or []]
        agent = " and ".join(names) or "An agent"
        head = f"{agent} has a Slate task waiting"
        body = f"{title}: open {agent} and say “work Slate”"
    elif reason == "due":
        head, body = ("Overdue" if ev.type == "item.overdue" else "Due now"), title
    elif reason == "reply":
        head, body = f"{who} replied", f"{title}: {(after.get('body_md') or '')[:140]}"
    elif reason == "comment":
        head, body = f"{who} commented", f"{title}: {(after.get('body_md') or '')[:140]}"
    elif reason == "watching":
        head, body = f"{who} {watch_verb(ev)}", title
    else:
        head, body = f"{who} changed {title}", "Open Slate to see what changed"
    return {
        "title": head[:120],
        "body": body[:240],
        "url": url,
        "tag": ev.item_id or f"{reason}:{ev.id}",
        "event_id": ev.id,
        "reason": reason,
    }


# --- The engine step -----------------------------------------------------------------------


def _cursor(session: Session) -> EngineCursor:
    row = session.get(EngineCursor, CURSOR)
    if row is None:
        row = EngineCursor(
            name=CURSOR, event_id=session.scalar(select(func.max(Event.id))), updated_at=utcnow()
        )
        session.add(row)
        session.commit()
    return row


def reasons_for(account: Account) -> frozenset[str]:
    chosen = (account.prefs_json or {}).get("push_reasons")
    return frozenset(chosen if isinstance(chosen, list) else DEFAULT_REASONS)


# Inbox reasons about a followed item that `watching` also pushes when the account watches it.
FOLLOW_REASONS = inbox.FOLLOW_REASONS - {"watching"}
# Description autosaves: one push per author and item until they pause this long.
EDIT_QUIET = timedelta(minutes=10)


def started_work(ev: Event) -> bool:
    """An agent's report that it started (`working`). It stays in the Inbox and the activity,
    but is never pushed or emailed: it follows the watcher's own message by a second, and the
    report that ends the run (review, done, blocked) is the one worth an interruption."""
    after = ev.after_json if isinstance(ev.after_json, dict) else {}
    return ev.type == "item.reported" and after.get("state") == "working"


def wants(account: Account, reason: str | None, ev: Event, f: inbox.Follows) -> bool:
    """Whether `account` is pushed `ev` for `reason`: chosen in its push reasons, or `watching`
    chosen and this is any activity on an item it watches."""
    return wanted(reasons_for(account), reason, ev, f)


def wanted(chosen: frozenset[str], reason: str | None, ev: Event, f: inbox.Follows) -> bool:
    """`wants` for any set of chosen reasons (push or email)."""
    if reason == "watching" and started_work(ev):
        return False
    if reason in chosen:
        return True
    return "watching" in chosen and reason in FOLLOW_REASONS and ev.item_id in f.watched


def _repeat_edit(session: Session, ev: Event) -> bool:
    """A description save shortly after the same author's last one on the same item."""
    if ev.type != "item.updated" or ev.field != "body_md":
        return False
    prev = session.scalar(
        select(Event.at)
        .where(
            Event.item_id == ev.item_id,
            Event.type == "item.updated",
            Event.field == "body_md",
            Event.actor_id == ev.actor_id,
            Event.id < ev.id,
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    return prev is not None and ev.at - prev < EDIT_QUIET


def process(session: Session, send: Sender | None) -> int:
    """Queue recent new events durably, then attempt at most DELIVERY_BATCH due devices.

    Cursor advancement and enqueue commit together, once per page of events, before any
    network I/O. Successful recipients stay terminal; a restart retries only pending
    deliveries, under fresh access.
    """
    session.expire_all()
    cursor = _cursor(session)
    top = session.scalar(select(func.max(Event.id)))
    now = utcnow()
    subs = list(session.scalars(select(PushSubscription))) if send is not None else []
    if subs and top:
        accounts = {a.id: a for a in session.scalars(select(Account))}
        gates = {aid: access.EventGate(aid) for aid in {s.account_id for s in subs}}
        for gate in gates.values():
            gate.load(session)
        follows = {aid: inbox.follows(session, aid) for aid in gates}
        while True:
            q = select(Event).where(Event.id <= top, Event.at >= now - DELIVERY_WINDOW)
            if cursor.event_id:
                q = q.where(Event.id > cursor.event_id)
            page = list(session.scalars(q.order_by(Event.id).limit(PAGE)))
            if not page:
                break
            for ev in page:
                eligible = []
                if ev.type == "item.watched" and ev.after_json in follows:
                    # A new watcher hears about what follows in this same batch.
                    follows[ev.after_json] = inbox.follows(session, ev.after_json)
                repeat = _repeat_edit(session, ev)
                for sub in subs:
                    account = accounts.get(sub.account_id)
                    if account is None or account.disabled_at or sub.created_at > ev.at:
                        continue
                    gate = gates[sub.account_id]
                    if gate.project(session, event_payload(ev)) is None:
                        continue
                    reason = inbox.reason_for(ev, account, follows[account.id]) or chat_reason(
                        ev, account
                    )
                    if (
                        not repeat
                        and wants(account, reason, ev, follows[account.id])
                        and not (
                            reason in inbox.FOLLOW_REASONS
                            and inbox.waiting_covers(
                                ev, reason, inbox.waits(session, account.id, [ev.item_id or ""])
                            )
                        )
                    ):
                        eligible.append(sub)
                for sub in eligible:
                    _enqueue(session, ev, sub, now=now)
            # Receipts and the cursor are bookkeeping: one commit per page, before any send.
            cursor.event_id, cursor.updated_at = page[-1].id, now
            session.commit()
            if len(page) < PAGE:
                break
    if top and cursor.event_id != top:
        cursor.event_id, cursor.updated_at = top, now
        session.commit()
    return _drain(session, send, now) if send is not None else 0


def _enqueue(
    session: Session, ev: Event, sub: PushSubscription, *, now: datetime, kind: str = "notification"
) -> None:
    """Add one receipt (bookkeeping; the caller commits). No payload, endpoint or key is
    stored: a receipt is the event id, the device, the account and the retry state."""
    key = (sub.id, ev.id, kind)
    if session.get(PushDelivery, key) is not None:
        return
    session.add(
        PushDelivery(
            subscription_id=sub.id,
            event_id=ev.id,
            kind=kind,
            account_id=sub.account_id,
            attempts=0,
            state="pending",
            next_attempt_at=now,
            expires_at=min(now, ev.at) + DELIVERY_WINDOW,
        )
    )


def _send(
    send: Sender, sub: PushSubscription, note: dict[str, Any]
) -> tuple[int | None, str, str | None]:
    try:
        result = send(sub.endpoint, sub.p256dh, sub.auth, note)
        return result[0], result[1], result[2] if len(result) > 2 else None
    except Exception as exc:
        # Transport failures are transient; never persist the URL/credentials in exception text.
        return None, type(exc).__name__, None


def _retry_delay(attempts: int, retry_after: str | None, now: datetime) -> timedelta:
    seconds = min(30 * 4 ** (attempts - 1), 3600)
    if retry_after:
        try:
            requested = float(retry_after)
        except ValueError:
            try:
                requested = (parsedate_to_datetime(retry_after) - now).total_seconds()
            except (ValueError, TypeError, OverflowError):
                requested = 0
        if math.isfinite(requested):
            seconds = max(seconds, min(requested, 6 * 3600))
    return timedelta(seconds=seconds)


def _drain(session: Session, send: Sender, now: datetime) -> int:
    rows = list(
        session.scalars(
            select(PushDelivery)
            .where(PushDelivery.next_attempt_at <= now)
            .order_by(PushDelivery.next_attempt_at, PushDelivery.event_id)
            .limit(DELIVERY_BATCH)
        )
    )
    sent = 0
    for row in rows:
        session.expire_all()  # permissions/endpoint ownership can change between attempts
        sub = session.get(PushSubscription, row.subscription_id)
        ev = session.get(Event, row.event_id)
        account = session.get(Account, row.account_id)
        gate = access.EventGate(row.account_id)
        gate.load(session)
        f = inbox.follows(session, account.id) if account else None
        reason = (
            (inbox.reason_for(ev, account, f) or chat_reason(ev, account))
            if ev and account and f
            else None
        )
        if row.kind == "renudge":
            reason = "chat"
        allowed = bool(
            sub
            and sub.account_id == row.account_id
            and ev
            and account
            and not gate.denied
            and gate.project(session, event_payload(ev)) is not None
            and f is not None
            and wants(account, reason, ev, f)
            and now < row.expires_at
        )
        note = None
        if allowed and row.kind == "renudge":
            unread = next(
                (u for u in unread_by_channel(session, account) if u.newest_id == ev.subject_id),
                None,
            )
            if (
                unread is not None
                and renudge_minutes(account) > 0
                and "chat" in reasons_for(account)
            ):
                note = renudge_notification(session, unread)
        elif allowed:
            # Apply the recipient's read criteria to all reference rendering, even retries.
            with access.unrestricted(session):
                if gate.access is not None:
                    access.attach(session, gate.access)
                try:
                    note = notification_for(session, ev, reason)
                finally:
                    session.info.pop(access.ACCESS_KEY, None)
        if note is None:
            row.state, row.next_attempt_at = "dropped", None
            session.commit()
            continue
        status, error, retry_after = _send(send, sub, note)
        ok = status is not None and 200 <= status < 300 and not error
        transient = status is None or status in (408, 429) or status >= 500
        row.attempts += 1
        row.state = (
            "sent" if ok else "pending" if transient and row.attempts < MAX_ATTEMPTS else "failed"
        )
        row.next_attempt_at = (
            now + _retry_delay(row.attempts, retry_after, now) if row.state == "pending" else None
        )
        if row.next_attempt_at and row.next_attempt_at >= row.expires_at:
            row.state, row.next_attempt_at = "failed", None
        sent += _record(
            session,
            sub,
            status,
            error or ("" if ok else "delivery failed"),
            {sub.account_id: [sub]},
            commit=False,
        )
        # One commit right after each send (at-most-once): a crash between the two can
        # duplicate that one device, never replay the batch.
        session.commit()
    # Bounded retention and bounded cleanup; completed receipts only disappear after their
    # event can no longer be enqueued by the age gate (including after a cursor reset).
    expired = list(
        session.scalars(
            select(PushDelivery).where(PushDelivery.expires_at <= now).limit(DELIVERY_BATCH)
        )
    )
    for row in expired:
        session.delete(row)
    if expired:
        session.commit()
    return sent


def _record(
    session: Session,
    sub: PushSubscription,
    status: int | None,
    error: str,
    by_account: dict[str, list[PushSubscription]],
    *,
    commit: bool = True,
) -> int:
    """Book the outcome on the subscription (bookkeeping). Returns 1 if it was delivered."""
    if not error:
        sub.last_ok_at, sub.failures = utcnow(), 0
        if commit:
            session.commit()
        return 1
    failures = (sub.failures or 0) + 1
    if status in (404, 410) or failures >= MAX_FAILURES:
        by_account[sub.account_id].remove(sub)
        _expire(session, sub, error)
        return 0
    log.warning("push to %s failed: HTTP %s", urlsplit(sub.endpoint).netloc, status)
    sub.failures = failures
    if commit:
        session.commit()
    return 0


def send_test(session: Session, account_id: str, send: Sender) -> list[dict[str, Any]]:
    """Push a test notification to every device of the account; one result per device."""
    out = []
    note = {
        "title": "Slate notifications are on",
        "body": "This device will hear about mentions, assignments, reminders and your day.",
        "url": "/#/inbox",
        "tag": "slate-test",
        "reason": "test",
    }
    subs: Sequence[PushSubscription] = list_subscriptions(session, account_id)
    by_account = {account_id: list(subs)}
    for sub in subs:
        status, error, _ = _send(send, sub, note)
        view = subscription_view(sub)
        _record(session, sub, status, error, by_account)
        out.append({**view, "ok": not error, "error": error or None})
    return out


# --- The re-nudge: chat still unread a while after its newest message --------------------


def renudge_minutes(account: Account) -> int:
    value = (account.prefs_json or {}).get("chat_renudge_minutes", RENUDGE_DEFAULT_MINUTES)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@dataclass
class Unread:
    """A channel's unread messages for one account: how many, the newest, and who wrote them."""

    channel: str
    count: int
    newest_id: str
    newest_at: datetime
    authors: list[str]


def unread_by_channel(session: Session, account: Account) -> list[Unread]:
    # The same predicate drives channel badges and delivery retries: exposed messages
    # must not be resurrected by the older prefix-only notification query.
    rows = (
        (m.channel, m.id, m.created_at, m.author_id) for m in chat.unread_messages(session, account)
    )
    out: dict[str, Unread] = {}
    for channel, mid, at, author in rows:
        u = out.get(channel)
        if u is None:
            out[channel] = Unread(channel, 1, mid, at, [author])
            continue
        u.count += 1
        u.newest_id, u.newest_at = mid, at
        if author not in u.authors:
            u.authors.append(author)
    return list(out.values())


def renudge_notification(session: Session, u: Unread) -> dict[str, Any]:
    ch = chat.parse_channel(session, u.channel)
    names = [_name(session, a) for a in u.authors[:3]]
    who = ", ".join(names) + (" and others" if len(u.authors) > 3 else "")
    plural = "message" if u.count == 1 else "messages"
    return {
        "title": f"Still unread: {chat.place(ch.key, ch.name)}"[:120],
        "body": f"{u.count} {plural} from {who}, waiting since you last looked"[:240],
        "url": f"/#/chat/{u.channel}",
        "tag": f"chat:{u.channel}",  # replaces the channel's first toast
        "reason": "chat",
        "renudge": True,
    }


# (account id, channel) -> the newest unread message id already nudged (or seen at start).
# Restart seeds unqueued nudges from what is unread, so old chat is not replayed. Once queued,
# a nudge's delivery is durable; retries also recheck that the same chat is still unread.
Nudged = dict[tuple[str, str], str]


def renudge(
    session: Session,
    send: Sender | None,
    nudged: Nudged,
    *,
    now: datetime | None = None,
    seed: bool = False,
) -> int:
    """One more push per channel whose newest unread message is older than the account's
    `chat_renudge_minutes` and has not been nudged. `seed=True` marks what is eligible now
    without sending (the first run after a start). Returns pushes sent."""
    now = now or utcnow()
    session.expire_all()
    subs = list(session.scalars(select(PushSubscription))) if send is not None else []
    by_account: dict[str, list[PushSubscription]] = {}
    for s in subs:
        by_account.setdefault(s.account_id, []).append(s)
    if not by_account:
        return 0
    accounts = session.scalars(select(Account).where(Account.id.in_(by_account))).all()
    for account in accounts:
        if account.disabled_at is not None:
            continue
        minutes = renudge_minutes(account)
        if minutes <= 0 or "chat" not in reasons_for(account):
            continue
        with access.unrestricted(session):
            acc = access.compute(session, account)
        for u in unread_by_channel(session, account):
            if not acc.unrestricted and acc.channel_level(u.channel) <= 0:
                continue
            key = (account.id, u.channel)
            if nudged.get(key) == u.newest_id:
                continue
            age = now - u.newest_at
            if age < timedelta(minutes=minutes):
                continue
            nudged[key] = u.newest_id
            if seed or age > RENUDGE_WINDOW:
                continue
            ev = session.scalar(
                select(Event).where(Event.type == "message.posted", Event.subject_id == u.newest_id)
            )
            if ev is not None:
                for sub in by_account.get(account.id, []):
                    _enqueue(session, ev, sub, now=now, kind="renudge")
                session.commit()
    return _drain(session, send, now) if not seed else 0
