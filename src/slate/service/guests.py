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
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import SYSTEM_HANDLE, Account, AccountKind, Role
from slate.service.errors import Forbidden

_accounts = Account.__table__


def workspace_agent(account: Account | None) -> bool:
    """An agent that belongs to the workspace rather than to one project: any agent account
    that is not a guest. `workspace_agents()` is the same rule in SQL."""
    return (
        account is not None
        and account.kind == AccountKind.agent
        and account.role != Role.guest
        and account.handle != SYSTEM_HANDLE  # Slate's own account is not an agent here
    )


def _is_workspace_agent() -> tuple[Any, ...]:
    """`workspace_agent` as SQL conditions on the accounts table. Table columns, not the
    mapped class, so the read filter never narrows it inside another query."""
    return (
        _accounts.c.kind == AccountKind.agent,
        _accounts.c.role != Role.guest,
        _accounts.c.handle != SYSTEM_HANDLE,
    )


def workspace_agents() -> Any:
    """SQL: the ids of the workspace agents (`workspace_agent`), for `IN` and `NOT IN`."""
    return select(_accounts.c.id).where(*_is_workspace_agent())


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
        return set(session.scalars(workspace_agents()))


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
            select(_accounts.c.handle).where(
                _accounts.c.handle.in_(handles), *_is_workspace_agent()
            )
        )
    )
    return [h for h in handles if h not in blocked]
