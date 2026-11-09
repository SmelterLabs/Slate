"""Guest browser sessions ending: the notice three days before, to the guest and the inviter.

A browser session (a `tokens` row with `browser_session`, minted by an accepted invitation)
expires thirty days after acceptance and a 401 then signs the browser out. So that day 31 is
not a surprise, the scheduler hook `notify_expiring()` records, once per session and expiry,
`session.expiring` for the guest ("your access to <section> ends on <date>; ask <inviter> for
a new link") and one for the inviter (`created_by` of the invitation). Both land in the inbox
(reason `access`) and in push. Idempotent across restarts: the token row keeps
`expiry_notified_at`; a notice is due while `expires_at - NOTICE_BEFORE <= now < expires_at`
and the marker is older than that window, so an admin's extension (`collaboration.extend`,
which moves `expires_at`) re-arms it for the new end.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.dates import LOCAL
from slate.models import Account, Invitation, Section, Token, utcnow
from slate.service.core import apply_change, jsonable

NOTICE_BEFORE = timedelta(days=3)


def _notice(
    session: Session, token: Token, account: Account
) -> tuple[dict[str, Any], Account | None]:
    invitation = session.scalar(
        select(Invitation).where(Invitation.token_id == token.id).order_by(Invitation.id.desc())
    )
    section = session.get(Section, invitation.section_id) if invitation else None
    inviter = session.get(Account, invitation.created_by) if invitation else None
    after = {
        "token_id": token.id,
        "account_id": account.id,
        "account": account.handle,
        "account_name": account.display_name,
        "section_id": section.id if section else None,
        "section": section.name if section else "the project",
        "inviter_id": inviter.id if inviter else None,
        "inviter": inviter.handle if inviter else None,
        "inviter_name": inviter.display_name if inviter else None,
        "expires_at": jsonable(token.expires_at),
        "date": token.expires_at.astimezone(LOCAL).date().isoformat(),
    }
    return after, inviter


def notify_expiring(session: Session, now: datetime | None = None) -> int:
    """Scheduler hook: tell the guest and the inviter once that a session ends in three days."""
    from slate.service.due import system_actor

    now = now or utcnow()
    tokens = session.scalars(
        select(Token).where(
            Token.browser_session.is_(True),
            Token.revoked_at.is_(None),
            Token.expires_at.is_not(None),
            Token.expires_at > now,
            Token.expires_at <= now + NOTICE_BEFORE,
        )
    ).all()
    sent = 0
    for token in tokens:
        window = token.expires_at - NOTICE_BEFORE
        if token.expiry_notified_at is not None and token.expiry_notified_at >= window:
            continue
        account = session.get(Account, token.account_id)
        if account is None or account.disabled_at:
            continue
        after, inviter = _notice(session, token, account)
        with apply_change(session, system_actor(session)) as cx:
            token.expiry_notified_at = now
            cx.record(
                "session.expiring",
                subject_id=account.id,
                after={**after, "for": "guest"},
                covers=[token],
            )
            if inviter is not None and inviter.id != account.id and not inviter.disabled_at:
                cx.record(
                    "session.expiring", subject_id=inviter.id, after={**after, "for": "inviter"}
                )
        sent += 1
    return sent
