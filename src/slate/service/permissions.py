"""Roles, shares and privacy: the writes behind service/access.py, plus the reads the Members
page needs. Every change here is an event (`account.role_changed`, `share.granted`,
`share.changed`, `share.revoked`, `section.updated`/`list.updated` on `private`).

Who may do what:

- Only an owner or admin changes roles, shares or privacy.
- Only the owner makes someone an admin or owner, or demotes an admin. Making someone else
  owner hands ownership over: the old owner becomes an admin. There is always one owner.
- Nobody changes their own role (except by handing ownership over).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.models import (
    Account,
    AccountKind,
    Event,
    Invitation,
    List,
    Role,
    Section,
    Share,
    ShareLevel,
    utcnow,
)
from slate.service import access
from slate.service.accounts import Principal, get_account
from slate.service.core import Actor, apply_change
from slate.service.errors import Forbidden, Invalid, NotFound

SCOPE_KINDS = ("section", "list")


def _role(account: Account) -> Role:
    return Role(account.role or Role.member)


def _require_manager(by: Principal) -> None:
    if _role(by.account) not in (Role.owner, Role.admin) or not by.has("admin"):
        raise Forbidden("only an owner or admin can manage members and sharing")


def retire_links(cx: Any, account_id: str, section_id: str) -> None:
    """Cancel the unused invitation links for one guest and section (`invitation.revoked`):
    a link must never give back a section share that was taken away."""
    pending = cx.session.scalars(
        select(Invitation).where(
            Invitation.account_id == account_id,
            Invitation.section_id == section_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    )
    for row in list(pending):
        cx.set(row, "revoked_at", utcnow(), "invitation.revoked")


def _scope(session: Session, scope_kind: str, scope_id: str) -> Section | List:
    if scope_kind not in SCOPE_KINDS:
        raise Invalid("scope_kind must be 'section' or 'list'")
    model = Section if scope_kind == "section" else List
    obj = session.get(model, scope_id)
    if obj is None:
        raise NotFound(f"{scope_kind} {scope_id} not found")
    return obj


# --- Roles ---------------------------------------------------------------------------------


def set_role(
    session: Session, actor: Actor, *, by: Principal, account_id: str, role: str
) -> Account:
    _require_manager(by)
    try:
        new = Role(role)
    except ValueError:
        raise Invalid("role must be owner, admin, member or guest") from None
    account = get_account(session, account_id)
    old = _role(account)
    mine = _role(by.account)
    if old == new:
        return account
    if account.id == by.account.id:
        raise Invalid("you cannot change your own role")
    if mine != Role.owner and (new in (Role.owner, Role.admin) or old in (Role.owner, Role.admin)):
        raise Forbidden("only the owner can make or unmake admins")
    with apply_change(session, actor) as cx:
        cx.set(account, "role", new, "account.role_changed")
        if new == Role.owner:
            me = get_account(session, by.account.id)
            cx.set(me, "role", Role.admin, "account.role_changed")
    return account


# --- Shares --------------------------------------------------------------------------------


def list_shares(
    session: Session,
    *,
    account_id: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
) -> list[Share]:
    q = select(Share)
    if account_id:
        q = q.where(Share.account_id == account_id)
    if scope_kind:
        q = q.where(Share.scope_kind == scope_kind)
    if scope_id:
        q = q.where(Share.scope_id == scope_id)
    return list(session.scalars(q.order_by(Share.created_at, Share.id)))


def share(
    session: Session,
    actor: Actor,
    *,
    by: Principal,
    account_id: str,
    scope_kind: str,
    scope_id: str,
    level: str | None,
) -> Share | None:
    """Grant (or change, or with `level=None` revoke) one account's access to a section or
    list. Returns the share, or None when revoked."""
    _require_manager(by)
    _scope(session, scope_kind, scope_id)
    account = get_account(session, account_id)
    if level is not None:
        try:
            level = ShareLevel(level)
        except ValueError:
            raise Invalid("level must be view, comment or edit") from None
    existing = session.scalar(
        select(Share).where(
            Share.account_id == account.id,
            Share.scope_kind == scope_kind,
            Share.scope_id == scope_id,
        )
    )
    if level is not None and existing is None:
        _one_project(session, account, scope_kind, scope_id)
    with apply_change(session, actor) as cx:
        changed = (
            existing is not None if level is None else existing is None or existing.level != level
        )
        if changed and scope_kind == "section":
            cx.record("section.participants_changed", subject_id=scope_id)
        if level is None:
            if existing is not None:
                cx.delete(existing, "share.revoked")
            if scope_kind == "section":
                retire_links(cx, account.id, scope_id)
            return None
        if existing is None:
            return cx.create(
                Share(
                    account_id=account.id,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    level=level,
                    created_by=actor.account_id,
                ),
                "share.granted",
            )
        cx.set(existing, "level", level, "share.changed")
        return existing


def _one_project(session: Session, account: Account, scope_kind: str, scope_id: str) -> None:
    """A project's agent (a guest-role agent) works in one project: its own chat is that
    project's conversation with it, so a second project would read the first one's."""
    if account.kind != AccountKind.agent or _role(account) != Role.guest:
        return
    target = (
        scope_id
        if scope_kind == "section"
        else session.scalar(select(List.section_id).where(List.id == scope_id))
    )
    others = access.agent_projects(session, account.id) - {target}
    if others:
        raise Invalid(
            f"{account.display_name} is already the agent of another project; a project agent "
            "works in one project only (its chat is that project's). Remove it there first, or "
            "add another agent for this project"
        )


def revoke_share(session: Session, actor: Actor, *, by: Principal, share_id: str) -> None:
    _require_manager(by)
    row = session.get(Share, share_id)
    if row is None:
        raise NotFound(f"share {share_id} not found")
    with apply_change(session, actor) as cx:
        if row.scope_kind == "section":
            cx.record("section.participants_changed", subject_id=row.scope_id)
            retire_links(cx, row.account_id, row.scope_id)
        cx.delete(row, "share.revoked")


def set_private(
    session: Session, actor: Actor, *, by: Principal, scope_kind: str, scope_id: str, private: bool
) -> Section | List:
    _require_manager(by)
    obj = _scope(session, scope_kind, scope_id)
    with apply_change(session, actor) as cx:
        cx.set(obj, "private", bool(private), f"{scope_kind}.updated")
    return obj


# --- Reads for the Members page ------------------------------------------------------------


def effective(session: Session, account_id: str) -> dict[str, Any]:
    """What `account_id` can reach with an unlimited token: its role, and every section and
    list with the level (`None` lists = everything)."""
    account = get_account(session, account_id)
    with access.unrestricted(session):
        found = access.compute(session, account)
        if found.lists is None:
            return {
                "account_id": account.id,
                "role": _role(account),
                "all": True,
                "sections": {},
                "lists": {},
            }
        return {
            "account_id": account.id,
            "role": _role(account),
            "all": False,
            "sections": {
                sid: access.LEVEL_NAME[lvl] for sid, lvl in (found.sections or {}).items() if lvl
            },
            "lists": {lid: access.LEVEL_NAME[lvl] for lid, lvl in found.lists.items()},
        }


def who_can_see(session: Session, scope_kind: str, scope_id: str) -> list[dict[str, Any]]:
    """Everyone who reaches a section or list, and how: the sharing dialog's list. Each row is
    `{account_id, level, via}` where `via` is `role`, `section`, `list` or `share`."""
    obj = _scope(session, scope_kind, scope_id)
    out: list[dict[str, Any]] = []
    with access.unrestricted(session):
        for account in session.scalars(
            select(Account).where(Account.disabled_at.is_(None)).order_by(Account.handle)
        ):
            found = access.compute(session, account)
            if scope_kind == "list":
                level = found.list_level(obj.id)
            else:
                level = found.section_level(obj.id)
            if not level:
                continue
            row = session.scalar(
                select(Share).where(
                    Share.account_id == account.id,
                    Share.scope_kind == scope_kind,
                    Share.scope_id == obj.id,
                )
            )
            if _role(account) in (Role.owner, Role.admin):
                via = "role"
            elif row is not None and access.level_rank(row.level) == level:
                via = "share"
            else:
                via = "section" if scope_kind == "list" else "role"
            out.append(
                {
                    "account_id": account.id,
                    "level": access.LEVEL_NAME[level],
                    "via": via,
                    "share_id": row.id if row is not None else None,
                }
            )
    return out


def members(session: Session) -> list[dict[str, Any]]:
    """Every account with role, kind, share count and last activity, for the Members page."""
    counts = dict(
        session.execute(select(Share.account_id, func.count()).group_by(Share.account_id)).all()
    )
    last = dict(
        session.execute(select(Event.actor_id, func.max(Event.at)).group_by(Event.actor_id)).all()
    )
    return [
        {
            "account": a,
            "role": _role(a),
            "shares": counts.get(a.id, 0),
            "last_active_at": last.get(a.id),
        }
        for a in session.scalars(select(Account).order_by(Account.handle))
    ]
