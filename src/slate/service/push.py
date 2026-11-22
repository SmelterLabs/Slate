"""Web Push: the browsers and installed apps that get notifications, and what they are told.

A subscription (`push_subscriptions`) is one browser's endpoint and keys for one account; the
board makes one when someone turns notifications on for that device (`push.subscribed`) and
removes it when they turn them off (`push.unsubscribed`). Its keys are never in an event.

The account's inbox supplies the reason. Attention supplies the level and releases alerts
when any hold ends. Durable device receipts can retry after restart. Every attempt
rechecks access and preferences before rendering under the recipient's read filter.

Receipts are bookkeeping (`core.BOOKKEEPING_MODELS`): the table is
the delivery log and the retry state, so queueing, sending, failing and expiring a receipt
writes no event.
"""

from __future__ import annotations

import contextlib
import fnmatch
import logging
import math
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from slate import outbound, webpush
from slate.ids import new_id
from slate.models import (
    Account,
    AttentionAlert,
    Event,
    Item,
    PushDelivery,
    PushSubscription,
    Source,
    utcnow,
)
from slate.service import access, attention, chat, comments
from slate.service.core import Actor, apply_change
from slate.service.errors import Forbidden, Invalid, NotFound
from slate.settings import settings

log = logging.getLogger(__name__)

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
    "failed",
    "stalled",
    "watching",
)

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
    with contextlib.suppress(outbound.LookupFailed):
        outbound.check_target(endpoint)
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


def notification_for(session: Session, ev: Event, reason: str) -> dict[str, Any] | None:
    """Title, body, the link a tap opens, and a tag (one notification per item replaces the
    last on the lock screen)."""
    if ev.type == "inbox.escalated":
        account = session.get(Account, ev.subject_id)
        original = attention.original_event(session, ev, account) if account else None
        note = (
            notification_for(session, original, reason)
            if original and attention.pending(session, original, account, reason)
            else None
        )
        return (
            {**note, "title": f"Still waiting: {note['title']}"[:120], "event_id": ev.id}
            if note
            else None
        )
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
    if ev.type == "item.unparked":
        note = after.get("note") or ""
        text = f"Back today: {title} (was waiting on {note})" if note else f"Back today: {title}"
        head, body = text, text
    elif reason == "reminder":
        head, body = f"⏰ {title}", after.get("note") or "Reminder"
    elif reason == "digest":
        after = access.digest_snapshot(session, after, access.current(session))
        questions = after.get("questions") or []
        if not questions and not any((after.get("counts") or {}).values()):
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
        if questions:
            body = (
                "; ".join(f"{q['question']} ({q['age_minutes']} min)" for q in questions)
                + ". "
                + body
            )
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
        # An agent's ask: the question is the headline, the card the body.
        ask = comments.asks_of(session, [item]).get(item.id) if item is not None else None
        if ask is not None:
            head, body = ask["question"], f"{who} asks on: {title}"
        else:
            head, body = f"{who} is waiting on you", title
    elif reason == "failed":
        head = f"{who} could not run: {title}"
        body = (after.get("note") or "").strip().splitlines()[0] if after.get("note") else title
    elif reason == "stalled":
        head, body = f"{who} stopped without finishing: {title}", "Run now carries it on"
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


def reasons_for(account: Account) -> frozenset[str]:
    chosen = (account.prefs_json or {}).get("push_reasons")
    return frozenset(chosen if isinstance(chosen, list) else DEFAULT_REASONS)


def process(session: Session, send: Sender | None, *, now: datetime | None = None) -> int:
    """Queue released alerts durably, then attempt due device deliveries."""
    now = now or utcnow()
    attention.release(session, now)
    if send is None:
        return 0
    subscriptions: dict[str, list[PushSubscription]] = {}
    for sub in session.scalars(select(PushSubscription)):
        subscriptions.setdefault(sub.account_id, []).append(sub)
    for aid, devices in subscriptions.items():
        account = session.get(Account, aid)
        if account is None or account.disabled_at:
            continue
        reasons = reasons_for(account)
        for alert in attention.released(
            session, account, since=now - DELIVERY_WINDOW, render=False
        ):
            if alert["reason"] not in reasons or alert["source_at"] + DELIVERY_WINDOW <= now:
                continue
            ev = session.get(Event, alert["event_id"])
            for sub in devices:
                if sub.created_at <= alert["source_at"]:
                    row = _receipt(session, ev, sub, now=now)
                    if row is not None:
                        session.add(row)
    session.commit()
    return _drain(session, send, now)


def _receipt(
    session: Session, ev: Event, sub: PushSubscription, *, now: datetime, kind: str = "notification"
) -> PushDelivery | None:
    """A new receipt, or None when there is one already. No payload, endpoint or key is
    stored: a receipt is the event id, the device, the account and the retry state."""
    key = (sub.id, ev.id, kind)
    if session.get(PushDelivery, key) is not None:
        return None
    return PushDelivery(
        subscription_id=sub.id,
        event_id=ev.id,
        kind=kind,
        account_id=sub.account_id,
        attempts=0,
        state="pending",
        next_attempt_at=now,
        expires_at=min(now, ev.at) + DELIVERY_WINDOW,
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


def _note_for(
    session: Session, row: PushDelivery, now: datetime
) -> tuple[PushSubscription | None, dict[str, Any] | None]:
    """The device and the notification for one receipt, judged now (None: drop it)."""
    sub = session.get(PushSubscription, row.subscription_id)
    account = session.get(Account, row.account_id)
    if not (
        sub
        and sub.account_id == row.account_id
        and account
        and not account.disabled_at
        and row.kind == "notification"
        and now < row.expires_at
    ):
        return sub, None
    release = session.get(AttentionAlert, (row.account_id, row.event_id))
    if release is None or release.reason not in reasons_for(account):
        return sub, None
    alerts = attention.released(session, account, event_id=row.event_id, limit=1)
    return sub, alerts[0] if alerts else None


def _drain(session: Session, send: Sender, now: datetime) -> int:
    rows = session.scalars(
        select(PushDelivery)
        .where(PushDelivery.next_attempt_at <= now)
        .order_by(PushDelivery.next_attempt_at, PushDelivery.event_id)
        .limit(DELIVERY_BATCH)
    ).all()
    sent = 0
    for row in rows:
        session.expire_all()
        release = session.get(AttentionAlert, (row.account_id, row.event_id))
        if release is not None and release.cursor is None and now < row.expires_at:
            continue
        try:
            sub, note = _note_for(session, row, now)
        except SQLAlchemyError:
            raise
        except Exception:
            log.exception("push: delivery of event %s could not be built", row.event_id)
            row.state, row.next_attempt_at, row.failed_at = "failed", None, now
            session.commit()
            continue
        if note is None:
            row.state, row.next_attempt_at = "dropped", None
            session.commit()
            continue
        status, error, retry_after = _send(send, sub, note)
        ok = status is not None and 200 <= status < 300 and not error
        transient = status is None or status in (408, 429) or status >= 500
        row.attempts += 1
        next_at = now + _retry_delay(row.attempts, retry_after, now)
        retry = not ok and transient and row.attempts < MAX_ATTEMPTS and next_at < row.expires_at
        row.state = "pending" if retry else "sent" if ok else "failed"
        row.next_attempt_at = next_at if retry else None
        row.failed_at = now if row.state == "failed" else None
        sent += _record(
            session,
            sub,
            status,
            error or ("" if ok else "delivery failed"),
            {sub.account_id: [sub]},
            commit=False,
        )
        session.commit()
    expired = session.scalars(
        select(PushDelivery)
        .where(
            PushDelivery.expires_at <= now,
            (PushDelivery.failed_at.is_(None)) | (PushDelivery.failed_at <= now - DELIVERY_WINDOW),
        )
        .limit(DELIVERY_BATCH)
    ).all()
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
