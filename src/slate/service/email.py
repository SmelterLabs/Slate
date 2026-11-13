"""Email notifications: what an account would be pushed, emailed only if it stays unseen.

An account opts in with two prefs: `email_to` (one address) and `email_reasons` (inbox
reasons, the same words as `push_reasons`; default `DEFAULT_REASONS` once an address is
set). `process()` reads events after the `engine_cursors` row `email` and queues one
`email_deliveries` row per event and recipient whose reason was chosen, due `UNSEEN_DELAY`
later. When it is due, the row is sent only if the account still has not seen it: the inbox
entry is not read, the card was not opened since, and a chat message is still unread. Every
due row for one account goes out together as one email, so a burst is one message, not ten.
Access and preferences are rechecked at send time, like push (service/push.py), and the text
is rendered under the recipient's read filter.

Receipts are bookkeeping (`core.BOOKKEEPING_MODELS`): queueing and sending write no event.
"""

from __future__ import annotations

import html
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.models import (
    Account,
    AccountKind,
    ChannelRead,
    EmailDelivery,
    EngineCursor,
    Event,
    InboxMark,
    InboxState,
    ItemRead,
    utcnow,
)
from slate.service import access, inbox, push
from slate.service.core import event_payload
from slate.settings import settings

log = logging.getLogger(__name__)

CURSOR = "email"
PAGE = 500
UNSEEN_DELAY = timedelta(minutes=5)
DELIVERY_WINDOW = timedelta(days=1)
MAX_ATTEMPTS = 4
RETRY_DELAY = timedelta(minutes=5)
DEFAULT_REASONS = ("mention", "waiting")
MAX_PER_EMAIL = 20

# (to, subject, text, html) -> None on success, else an error
Sender = Callable[[str, str, str, str], str | None]


def address(account: Account) -> str | None:
    to = (account.prefs_json or {}).get("email_to")
    return to if isinstance(to, str) and to else None


def reasons_for(account: Account) -> frozenset[str]:
    chosen = (account.prefs_json or {}).get("email_reasons")
    return frozenset(chosen if isinstance(chosen, list) else DEFAULT_REASONS)


def _recipients(session: Session) -> dict[str, Account]:
    return {
        a.id: a
        for a in session.scalars(select(Account).where(Account.disabled_at.is_(None)))
        if a.kind == AccountKind.person and address(a) and reasons_for(a)
    }


def _cursor(session: Session) -> EngineCursor:
    row = session.get(EngineCursor, CURSOR)
    if row is None:
        row = EngineCursor(
            name=CURSOR, event_id=session.scalar(select(func.max(Event.id))), updated_at=utcnow()
        )
        session.add(row)
        session.commit()
    return row


def _reason(session: Session, ev: Event, account: Account, f: inbox.Follows) -> str | None:
    """The account's reason for `ev` if it chose to be emailed for it, else None."""
    why = inbox.reason_for(ev, account, f)
    if not push.wanted(reasons_for(account), why, ev, f):
        return None
    if why in inbox.FOLLOW_REASONS and inbox.waiting_covers(
        ev, why, inbox.waits(session, account.id, [ev.item_id or ""])
    ):
        return None
    return why


def seen(session: Session, account: Account, ev: Event) -> bool:
    """Whether the account has seen `ev` on the board since it happened."""
    state = session.get(InboxState, account.id)
    if state and state.read_through and ev.id <= state.read_through:
        return True
    if session.get(InboxMark, (account.id, ev.id)) is not None:
        return True
    if ev.item_id:
        read = session.get(ItemRead, (account.id, ev.item_id))
        if read is not None and read.read_at >= ev.at:
            return True
    if ev.type.startswith("message.") and ev.subject_id:
        channel = (ev.after_json or {}).get("channel") if isinstance(ev.after_json, dict) else None
        row = session.get(ChannelRead, (account.id, channel)) if channel else None
        if row is not None and (
            ev.subject_id <= row.read_through or ev.subject_id in (row.exposed_ids or [])
        ):
            return True
    return False


def process(session: Session, send: Sender | None, now: datetime | None = None) -> int:
    """Queue new events for the accounts that chose them, then send what is due."""
    session.expire_all()
    now = now or utcnow()
    cursor = _cursor(session)
    top = session.scalar(select(func.max(Event.id)))
    recipients = _recipients(session)
    if recipients and top:
        gates = {aid: access.EventGate(aid) for aid in recipients}
        for gate in gates.values():
            gate.load(session)
        follows = {aid: inbox.follows(session, aid) for aid in recipients}
        while True:
            q = select(Event).where(Event.id <= top, Event.at >= now - DELIVERY_WINDOW)
            if cursor.event_id:
                q = q.where(Event.id > cursor.event_id)
            page = list(session.scalars(q.order_by(Event.id).limit(PAGE)))
            if not page:
                break
            for ev in page:
                if ev.type == "item.watched" and ev.after_json in follows:
                    follows[ev.after_json] = inbox.follows(session, ev.after_json)
                if push._repeat_edit(session, ev):
                    continue
                for aid, account in recipients.items():
                    if gates[aid].project(session, event_payload(ev)) is None:
                        continue
                    if _reason(session, ev, account, follows[aid]) is None:
                        continue
                    if session.get(EmailDelivery, (aid, ev.id)) is None:
                        session.add(
                            EmailDelivery(
                                account_id=aid,
                                event_id=ev.id,
                                attempts=0,
                                state="pending",
                                send_after=max(ev.at, now - UNSEEN_DELAY) + UNSEEN_DELAY,
                                expires_at=ev.at + DELIVERY_WINDOW,
                            )
                        )
            cursor.event_id, cursor.updated_at = page[-1].id, now
            session.commit()
            if len(page) < PAGE:
                break
    if top and cursor.event_id != top:
        cursor.event_id, cursor.updated_at = top, now
        session.commit()
    return _drain(session, send, now) if send is not None else 0


def _render(session: Session, account: Account, ev: Event, reason: str) -> dict | None:
    gate = access.EventGate(account.id)
    gate.load(session)
    if gate.denied or gate.project(session, event_payload(ev)) is None:
        return None
    with access.unrestricted(session):
        if gate.access is not None:
            access.attach(session, gate.access)
        try:
            return push.notification_for(session, ev, reason)
        finally:
            session.info.pop(access.ACCESS_KEY, None)


def _link(url: str) -> str:
    base = settings.public_url.rstrip("/")
    return f"{base}{url}" if base else url


def compose(notes: list[dict[str, Any]]) -> tuple[str, str, str]:
    """(subject, text, html) for one account's due notes, newest last."""
    subject = notes[0]["title"] if len(notes) == 1 else f"{len(notes)} things in Slate need you"
    lines, items = [], []
    for n in notes[:MAX_PER_EMAIL]:
        link = _link(n["url"])
        lines.append(f"{n['title']}\n{n['body']}\n{link}\n")
        items.append(
            '<li style="margin:0 0 14px">'
            f'<a href="{html.escape(link)}" style="font-weight:600;color:#2563eb;'
            f'text-decoration:none">{html.escape(n["title"])}</a>'
            f'<div style="color:#444;margin-top:2px">{html.escape(n["body"])}</div></li>'
        )
    more = len(notes) - MAX_PER_EMAIL
    if more > 0:
        lines.append(f"...and {more} more in your Slate inbox.")
        items.append(f'<li style="color:#666">…and {more} more in your Slate inbox.</li>')
    inbox_link = _link("/#/inbox")
    foot = "You get this because it is still unread in Slate. Change it under Notifications."
    text = "\n".join(lines) + f"\nInbox: {inbox_link}\n\n{foot}\n"
    body = (
        '<div style="font-family:system-ui,sans-serif;font-size:14px;line-height:1.4">'
        f'<ul style="list-style:none;padding:0;margin:0">{"".join(items)}</ul>'
        f'<p style="margin-top:18px"><a href="{html.escape(inbox_link)}">Open your inbox</a></p>'
        f'<p style="color:#888;font-size:12px">{html.escape(foot)}</p></div>'
    )
    # A title or display name may hold a line break; a mail header may not.
    return " ".join(subject.split())[:150], text, body


def _drain(session: Session, send: Sender, now: datetime) -> int:
    rows = list(
        session.scalars(
            select(EmailDelivery)
            .where(EmailDelivery.state == "pending", EmailDelivery.send_after <= now)
            .order_by(EmailDelivery.send_after, EmailDelivery.event_id)
            .limit(500)
        )
    )
    by_account: dict[str, list[EmailDelivery]] = defaultdict(list)
    for row in rows:
        by_account[row.account_id].append(row)
    sent = 0
    for aid, group in by_account.items():
        session.expire_all()
        account = session.get(Account, aid)
        to = address(account) if account and not account.disabled_at else None
        f = inbox.follows(session, aid) if to else None
        notes, sending = [], []
        for row in group:
            ev = session.get(Event, row.event_id)
            reason = _reason(session, ev, account, f) if ev and f is not None else None
            note = None
            if reason and now < row.expires_at and not seen(session, account, ev):
                note = _render(session, account, ev, reason)
            if note is None:
                row.state, row.send_after = "dropped", None
                continue
            notes.append(note)
            sending.append(row)
        session.commit()
        if not notes or to is None:
            continue
        subject, text, body = compose(notes)
        try:
            error = send(to, subject, text, body)
        except Exception as exc:  # noqa: BLE001  # one account's failure is that account's only
            error = f"{type(exc).__name__}: {exc}"[:300]
        for row in sending:
            row.attempts += 1
            if error is None:
                row.state, row.send_after = "sent", None
            elif row.attempts < MAX_ATTEMPTS and now + RETRY_DELAY < row.expires_at:
                row.send_after = now + RETRY_DELAY
            else:
                row.state, row.send_after = "failed", None
        if error:
            log.warning("email to account %s failed: %s", aid, error)
        else:
            sent += 1
        session.commit()
    expired = list(
        session.scalars(select(EmailDelivery).where(EmailDelivery.expires_at <= now).limit(500))
    )
    for row in expired:
        session.delete(row)
    if expired:
        session.commit()
    return sent


def send_test(account: Account, send: Sender) -> str | None:
    """Email the account a test message now; None when the server accepted it."""
    to = address(account)
    if to is None:
        return "set an email address first"
    note = {
        "title": "Slate email notifications are on",
        "body": "Slate emails you when something you chose is still unread after "
        f"{int(UNSEEN_DELAY.total_seconds() // 60)} minutes.",
        "url": "/#/inbox",
    }
    subject, text, body = compose([note])
    return send(to, subject, text, body)
