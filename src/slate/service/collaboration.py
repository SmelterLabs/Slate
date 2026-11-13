"""Private section invitations. Possession proves access to a link, NOT an email identity.

Seven-day, 256-bit single-use credentials become thirty-day, section-limited browser sessions.
Re-entry after logout/expiry needs a fresh admin-issued link. No email or password service.
"""

from __future__ import annotations

import hmac
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.ids import new_id
from slate.models import (
    Account,
    AccountKind,
    Invitation,
    Role,
    Section,
    Share,
    ShareLevel,
    Source,
    Token,
    utcnow,
)
from slate.service import access, accounts, chat, permissions, sections
from slate.service.core import Actor, apply_change, snapshot
from slate.service.errors import Gone, Invalid, NotFound

INVITE_RE = re.compile(r"^invite_([0-9A-Za-z]{12})_([A-Za-z0-9_-]{43})$")


def whiteboard_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        url = urlsplit(value)
        valid = url.scheme == "https" and url.hostname and not url.username and not url.password
        valid = valid and not any(c.isspace() or ord(c) < 32 for c in value) and "\\" not in value
        _ = url.port
    except ValueError:
        valid = False
    if not valid or len(value) > 2048:
        raise Invalid("whiteboard_url must be an HTTPS URL without credentials")
    return value


def issue(
    session: Session,
    actor: Actor,
    *,
    by: accounts.Principal,
    section_id: str,
    handle: str,
    display_name: str,
) -> tuple[Invitation, str]:
    permissions._require_manager(by)
    section = sections.get_section(session, section_id)
    if not section.private or section.archived_at:
        raise Invalid("choose an active private section before inviting a guest")
    account = session.scalar(select(Account).where(Account.handle == handle))
    if account and (
        account.role != Role.guest or account.kind != AccountKind.person or account.disabled_at
    ):
        raise Invalid("invitations are only for active guest person accounts")
    with apply_change(session, actor) as cx:
        if account is None:
            account = accounts.create_account(
                session,
                actor,
                handle=handle,
                display_name=display_name,
                kind="person",
                role="guest",
            )
        ident = new_id()
        secret = f"invite_{ident}_{secrets.token_urlsafe(32)}"
        row = cx.create(
            Invitation(
                id=ident,
                account_id=account.id,
                section_id=section.id,
                secret_hash=accounts.hash_token(secret),
                created_by=actor.account_id,
                expires_at=utcnow() + timedelta(days=7),
            ),
            "invitation.created",
        )
    return row, secret


def accept(session: Session, secret: str) -> tuple[Invitation, str]:
    match = INVITE_RE.fullmatch(secret)
    if not match:
        raise Gone("This invitation is invalid, expired, or already used. Ask for a new link.")
    # apply_change's process-wide writer lock spans validation, consumption, grant, and session.
    # Refresh under that lock: concurrent requests cannot both consume a cached pending row.
    with apply_change(session, Actor("pending", Source.ui)) as cx:
        row = session.get(Invitation, match[1], populate_existing=True)
        if (
            row is None
            or row.revoked_at
            or row.accepted_at
            or row.expires_at <= utcnow()
            or not hmac.compare_digest(row.secret_hash, accounts.hash_token(secret))
        ):
            raise Gone("This invitation is invalid, expired, or already used. Ask for a new link.")
        account = session.get(Account, row.account_id)
        section = session.get(Section, row.section_id)
        if (
            account is None
            or account.disabled_at
            or account.role != Role.guest
            or section is None
            or section.archived_at
            or not section.private
        ):
            raise Gone("This invitation is no longer available. Ask for a new link.")
        inviter = session.get(Account, row.created_by)
        if inviter is None or inviter.disabled_at or inviter.role not in (Role.owner, Role.admin):
            # Only a current owner or admin vouches for a guest, as when a connection is made.
            raise Gone("This invitation is no longer available. Ask for a new link.")
        cx.actor = Actor(account.id, Source.ui)
        grant = session.scalar(
            select(Share).where(
                Share.account_id == account.id,
                Share.scope_kind == "section",
                Share.scope_id == section.id,
            )
        )
        if grant is None:
            cx.create(
                Share(
                    account_id=account.id,
                    scope_kind="section",
                    scope_id=section.id,
                    level=ShareLevel.edit,
                    created_by=row.created_by,
                ),
                "share.granted",
            )
        # An existing share stays as the admin set it: a link neither raises nor lowers it.
        token, plaintext = accounts.create_token(
            session,
            cx.actor,
            account_id=account.id,
            scopes=["write"],
            label="Project browser session",
            # Edit at most, but no section pin: the session reaches exactly the guest's shares
            # (role guest sees nothing unshared), so a section shared later is visible without
            # a new link.
            limit={"level": "edit"},
        )
        cx.set(token, "expires_at", utcnow() + timedelta(days=30), "session.created")
        cx.set(token, "browser_session", True, "session.created")
        cx.record("section.participants_changed", subject_id=section.id)
        cx.set(row, "accepted_at", utcnow(), "invitation.accepted")
        cx.set(row, "token_id", token.id, "invitation.accepted")
    return row, plaintext


SESSION_DAYS = 30


def _invitation(session: Session, invitation_id: str) -> Invitation:
    row = session.get(Invitation, invitation_id, populate_existing=True)
    if row is None:
        raise NotFound("invitation not found")
    return row


def extend(
    session: Session, actor: Actor, *, by: accounts.Principal, invitation_id: str
) -> tuple[Invitation, Token]:
    """Push the accepted invitation's browser session out to thirty days from now."""
    permissions._require_manager(by)
    with apply_change(session, actor) as cx:
        row = _invitation(session, invitation_id)
        if row.revoked_at:
            raise Gone("this invitation was cancelled; issue a new link instead")
        if row.token_id is None:
            raise Invalid("the invitation has not been accepted yet, so there is no session")
        token = session.get(Token, row.token_id, populate_existing=True)
        if token is None or token.revoked_at:
            raise Gone("that session was signed out or revoked; issue a new link instead")
        cx.set(token, "expires_at", utcnow() + timedelta(days=SESSION_DAYS), "session.extended")
    return row, token


def reissue(
    session: Session, actor: Actor, *, by: accounts.Principal, invitation_id: str
) -> tuple[Invitation, str]:
    """A fresh single-use link for the same guest and section (another device, a link opened
    in the wrong app). The original stays valid until used, cancelled or expired; cancelling
    either, or removing the guest's section share, retires every unused link for both."""
    permissions._require_manager(by)
    with apply_change(session, actor) as cx:
        old = _invitation(session, invitation_id)
        if old.revoked_at:
            raise Gone("this invitation was cancelled; invite the guest again instead")
        account = session.get(Account, old.account_id)
        section = session.get(Section, old.section_id)
        if (
            account is None
            or account.disabled_at
            or account.role != Role.guest
            or account.kind != AccountKind.person
        ):
            raise Invalid("invitations are only for active guest person accounts")
        if section is None or section.archived_at or not section.private:
            raise Invalid("choose an active private section before inviting a guest")
        ident = new_id()
        secret = f"invite_{ident}_{secrets.token_urlsafe(32)}"
        row = Invitation(
            id=ident,
            account_id=account.id,
            section_id=section.id,
            secret_hash=accounts.hash_token(secret),
            created_by=actor.account_id,
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(days=7),
        )
        cx.create(row, "invitation.created", after={**snapshot(row), "reissued_from": old.id})
    return row, secret


def revoke(session: Session, actor: Actor, *, by: accounts.Principal, invitation_id: str) -> None:
    permissions._require_manager(by)
    with apply_change(session, actor) as cx:
        row = session.get(Invitation, invitation_id, populate_existing=True)
        if row is None:
            raise NotFound("invitation not found")
        cx.set(row, "revoked_at", utcnow(), "invitation.revoked")
        if row.token_id:
            accounts.revoke_token(session, actor, token_id=row.token_id, by=by)
        # The links copied for other devices go with it (their sessions, once accepted, are
        # separate: cancel those invitations, or remove the share, to end them).
        permissions.retire_links(cx, row.account_id, row.section_id)


def project(session: Session, section_id: str) -> dict:
    section = sections.get_section(session, section_id)
    caller = access.current(session)
    if caller and caller.section_level(section_id) == 0:
        raise NotFound("section project not found")
    participants = []
    entries = permissions.who_can_see(session, "section", section_id)
    ids = [entry["account_id"] for entry in entries]
    by_id = {a.id: a for a in session.scalars(select(Account).where(Account.id.in_(ids)))}
    for entry in entries:
        # No preferences, presence, token metadata, or shares elsewhere.
        account = by_id[entry["account_id"]]
        # A guest is not shown the workspace's own agents: what they say on a project
        # is members-only (service/chat.py `members_only_post`).
        if caller and caller.role == Role.guest and chat.workspace_agent(account):
            continue
        participants.append(
            {
                "id": account.id,
                "handle": account.handle,
                "display_name": account.display_name,
                "kind": account.kind,
                # guest: an account that sees only what is shared with it. A guest agent on a
                # project is that project's own agent (the board's Ask picker starts on it).
                "role": account.role,
                "level": entry["level"],
            }
        )
    return {"section": snapshot(section), "participants": participants}
