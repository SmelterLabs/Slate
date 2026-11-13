"""What a project's guests may do with the workspace's own agents.

A **workspace agent** is an agent account that is not a guest: it works for the
workspace's members. A project's own agent is a guest account shared into that project. A
project's guests talk with the project's agent and never with a workspace agent. They may read
what a workspace agent wrote on a card (chat between members and it is hidden from them,
`Message.members_only`), but nothing a guest does reaches it:

- an Ask addressed to it (card comment or doc thread), or a reassignment to it: `Forbidden`;
- assigning it to an item, or Run now on an item it is assigned to: `Forbidden`;
- an `@mention` of it by a guest (card comment, doc, chat) is dropped from the mentions that
  notify and wake, and a guest's reply is never its next turn;
- on a task assigned to it, what the task asks and when it runs (title, description,
  acceptance, checklists, due and start dates, reopening it once done): `Forbidden`, in the
  write check (`access._refuse_steering`), so no write path skips it. A guest's comments reach
  its brief only quoted, as information; everything else on the card (status, priority, tags,
  comments, files) stays the guest's to change.

Read-only helpers: no writes, no events.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, Role
from slate.service.errors import Forbidden

SYSTEM_HANDLE = "slate"  # Slate's own scheduler account (service/accounts.py)


def workspace_agent(account: Account | None) -> bool:
    """An agent that belongs to the workspace rather than to one project: any agent account
    that is not a guest."""
    return (
        account is not None
        and account.kind == AccountKind.agent
        and account.role != Role.guest
        and account.handle != SYSTEM_HANDLE  # Slate's own scheduler is not an agent here
    )


def is_guest(session: Session, account_id: str | None) -> bool:
    account = session.get(Account, account_id) if account_id else None
    return account is not None and account.role == Role.guest


def viewer_is_guest(session: Session) -> bool:
    """Whether this request reads as a guest (service/access.py's attached caller)."""
    from slate.service import access  # access imports models only; keep the import local

    found = access.current(session)
    return found is not None and found.role == Role.guest


def workspace_agent_ids(session: Session) -> set[str]:
    from slate.service import access  # noqa: PLC0415

    with access.unrestricted(session):
        return set(
            session.scalars(
                select(Account.id).where(
                    Account.kind == AccountKind.agent,
                    Account.role != Role.guest,
                    Account.handle != SYSTEM_HANDLE,
                )
            )
        )


def may_reach(session: Session, actor_id: str | None, target: Account | None) -> bool:
    """Whether the actor may address `target` (ask, assign, wake, notify)."""
    return not (workspace_agent(target) and is_guest(session, actor_id))


def refuse(session: Session, actor_id: str | None, target: Account | None) -> None:
    if not may_reach(session, actor_id, target):
        raise Forbidden(
            f"{target.display_name} works for this workspace's members; ask the project's "
            "agent instead"
        )


def reachable_handles(session: Session, actor_id: str | None, handles: Iterable[str]) -> list[str]:
    """`handles` without the workspace agents a guest actor may not reach."""
    handles = list(handles)
    if not handles or not is_guest(session, actor_id):
        return handles
    blocked = set(
        session.scalars(
            select(Account.handle).where(
                Account.handle.in_(handles),
                Account.kind == AccountKind.agent,
                Account.role != Role.guest,
                Account.handle != SYSTEM_HANDLE,
            )
        )
    )
    return [h for h in handles if h not in blocked]
