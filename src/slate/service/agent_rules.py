"""The agent action deny-list: one workspace rule naming what agent accounts may never do.

A token limits what an account reaches (role, shares, `Token.limit_json`); this limits what an
*agent* may do anywhere, whatever its token: an agent on an unlimited admin token, or a project
agent a guest is driving, is refused an action the workspace has ruled out. People are never
affected, owners and admins included.

The vocabulary is fixed (`ACTIONS`): each key names the event types (optionally `type:field`)
it covers. Enforcement is in the single change path: `core.Change` calls `check()` before every
create/set/delete/record, so REST, MCP (`LocalBackend`) and batch are all covered and nothing is
enforced in routes. `export` is a read, so `export.py` calls `check_read()` itself.

Not checked: Slate's own work (an actor with source `system`, or the `slate` account: purging
the trash after its days, automations), consequences recorded with `Change.derive`, and an
agent revoking its own token or connection. Changing the rule itself is never open to agents.

Stored as one `workspace_settings` row (`agents.denied`, a list of keys); with no row the
`DEFAULT` applies. A change is a `settings.changed` event with its actor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, Source, WorkspaceSetting
from slate.service.errors import Forbidden, Invalid

if TYPE_CHECKING:
    from slate.service.core import Actor

KEY = "agents.denied"


@dataclass(frozen=True)
class Rule:
    label: str  # "Agents may not …" on the board
    verb: str  # "agents may not <verb> here (workspace rule)"
    covers: frozenset[str]  # event types, or "type:field"


ACTIONS: dict[str, Rule] = {
    "delete": Rule(
        "delete tasks, docs or files for good, or empty the trash",
        "delete things for good",
        frozenset({"item.purged", "item.attachment_removed", "doc.deleted", "doc.file_removed"}),
    ),
    "trash": Rule(
        "move tasks to the trash",
        "move tasks to the trash",
        frozenset({"item.trashed"}),
    ),
    "archive_structure": Rule(
        "archive sections or lists",
        "archive sections or lists",
        frozenset({"section.archived", "list.archived"}),
    ),
    "share": Rule(
        "share, invite, change roles or make things private",
        "change sharing",
        frozenset(
            {
                "share.granted",
                "share.changed",
                "share.revoked",
                "invitation.created",
                "invitation.revoked",
                "session.extended",
                "account.role_changed",
                "section.updated:private",
                "list.updated:private",
            }
        ),
    ),
    "tokens": Rule(
        "issue or revoke tokens, connect agents, or create, disable or enable accounts",
        "issue tokens or manage accounts",
        frozenset(
            {
                "token.created",
                "token.revoked",
                "agent.connection_created",
                "agent.connection_revoked",
                "account.created",
                "account.disabled",
                "account.enabled",
            }
        ),
    ),
    "automations": Rule(
        "create or change automations and webhooks",
        "change automations or webhooks",
        frozenset(
            {
                "automation.created",
                "automation.updated",
                "automation.deleted",
                "automation.secret_rotated",
                "webhook.created",
                "webhook.updated",
                "webhook.disabled",
                "webhook.enabled",
            }
        ),
    ),
    "export": Rule(
        "export data or download the backup",
        "export",
        frozenset(),  # a read: export.py calls check_read("export")
    ),
}
DEFAULT: tuple[str, ...] = ("delete", "archive_structure", "share", "tokens")

# Event type (or "type:field") -> the key that covers it.
_COVERED: dict[str, str] = {c: key for key, rule in ACTIONS.items() for c in rule.covers}
_TYPES = frozenset(c.partition(":")[0] for c in _COVERED)
# Covered types an agent may still do to its own credential (obj.account_id is the actor).
_OWN_OK = frozenset({"token.revoked", "agent.connection_revoked"})


def _ordered(keys: Any) -> list[str]:
    return [k for k in ACTIONS if k in set(keys)]


def denied(session: Session) -> list[str]:
    """The keys agents may not do now: the stored list, else `DEFAULT`."""
    with session.no_autoflush:
        row = session.get(WorkspaceSetting, KEY)
    if row is None or not isinstance(row.value_json, list):
        return list(DEFAULT)
    return _ordered(row.value_json)


def view(session: Session) -> dict[str, Any]:
    with session.no_autoflush:
        stored = session.get(WorkspaceSetting, KEY) is not None
    return {
        "denied": denied(session),
        "default": not stored,
        "actions": [{"key": k, "label": r.label} for k, r in ACTIONS.items()],
    }


def _is_agent(session: Session, account_id: str) -> bool:
    from slate.service.due import SYSTEM_HANDLE

    with session.no_autoflush:
        account = session.get(Account, account_id)
    return (
        account is not None
        and account.kind == AccountKind.agent
        and account.handle != SYSTEM_HANDLE
    )


def _refuse(key: str) -> Forbidden:
    return Forbidden(f"agents may not {ACTIONS[key].verb} here (workspace rule)")


def covering(type_: str, field: str | None) -> str | None:
    """The deny-list key an event of `type_` (setting `field`) falls under, if any."""
    if type_ not in _TYPES:
        return None
    if field is not None and f"{type_}:{field}" in _COVERED:
        return _COVERED[f"{type_}:{field}"]
    return _COVERED.get(type_)


def check(
    session: Session, actor: Actor, type_: str, *, field: str | None = None, obj: Any = None
) -> None:
    """Raise `Forbidden` when `actor` is an agent and this write falls under a denied key.
    Costs nothing for event types no key covers (most of them)."""
    key = covering(type_, field)
    if key is None or actor.source == Source.system:
        return
    if type_ in _OWN_OK and getattr(obj, "account_id", None) == actor.account_id:
        return  # giving up your own token or connection is always allowed
    if not _is_agent(session, actor.account_id):
        return
    if key in denied(session):
        raise _refuse(key)


def check_read(session: Session, key: str) -> None:
    """For a covered read (`export`): refuse it when the session's caller is an agent and the
    key is denied. A session with no caller (scheduler, CLI) is Slate's own."""
    from slate.service import access

    caller = access.caller(session)
    if caller is None or not _is_agent(session, caller.account_id):
        return
    if key in denied(session):
        raise _refuse(key)


def set_denied(session: Session, actor: Actor, keys: list[str]) -> dict[str, Any]:
    """Replace the deny-list (owner or admin; never an agent, whatever its role)."""
    from slate.service import access
    from slate.service.core import apply_change

    unknown = sorted(set(keys) - set(ACTIONS))
    if unknown:
        raise Invalid(f"unknown action(s): {', '.join(unknown)}; known: {', '.join(ACTIONS)}")
    if _is_agent(session, actor.account_id):
        raise Forbidden("agents may not change the agent rules")
    access.require_admin(session, "change the agent rules")
    new = _ordered(keys)
    before = denied(session)
    with apply_change(session, actor) as cx:
        row = session.get(WorkspaceSetting, KEY)
        if row is None:
            row = WorkspaceSetting(key=KEY, value_json=new)
            session.add(row)
        elif row.value_json != new:
            row.value_json = new
        else:
            return view(session)
        cx.record("settings.changed", field=KEY, before=before, after=new, covers=[row])
    return view(session)
