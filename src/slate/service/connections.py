"""Single-use consent exchange and exact-credential connection lifecycle.

Pairing is not a share grant. The issuer and agent must already reach every selected
list at edit, and this is checked again under the single process write lock at exchange.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.connection_models import AgentConnection
from slate.ids import new_id
from slate.models import Account, AccountKind, Event, List, Role, Section, Source, Token, utcnow
from slate.runner_compat import MIN_RUNNER_PROTOCOL
from slate.service import access, accounts
from slate.service.accounts import Principal
from slate.service.core import Actor, apply_change
from slate.service.errors import Forbidden, Gone, Invalid, NotFound

CAPABILITIES = frozenset({"task", "chat", "doc", "resume", "cancel", "probe"})
CHECKS = {
    "connected": "Connected; agent test not run.",
    "ready": "Agent test passed; connection active.",
    "probe_passed": "Agent test passed; connection active.",
    "runtime_unavailable": "Agent runtime unavailable. Check the local command and installation.",
    "needs_setup": "Local setup needs attention. Run doctor on the agent computer.",
}
STALE = timedelta(seconds=120)
CONNECTION_EVENTS = {
    "created": "agent.connection_created",
    "connected": "agent.connection_connected",
    "checked": "agent.connection_checked",
    "revoked": "agent.connection_revoked",
}


def _manager(session: Session, by: Principal) -> bool:
    token = session.get(Token, by.token_id, populate_existing=True)
    account = session.get(Account, by.account.id, populate_existing=True)
    return bool(
        token
        and account
        and token.account_id == account.id
        and not account.disabled_at
        and not token.revoked_at
        and (not token.expires_at or token.expires_at > utcnow())
        and account.role in (Role.owner, Role.admin)
        and "admin" in token.scopes.split()
        and not token.limit_json
    )


def _agent_scope(session: Session, account_id: str, list_ids: list[str], issuer: Principal):
    agent = session.get(Account, account_id, populate_existing=True)
    if not agent or agent.kind != AccountKind.agent or agent.disabled_at or agent.handle == "slate":
        raise Invalid("Select an enabled agent account, not the system account.")
    if not list_ids or len(list_ids) > 100 or not all(isinstance(x, str) for x in list_ids):
        raise Invalid("Select at least one editable list (at most 100).")
    with access.unrestricted(session):
        agent_access = access.compute(session, agent)
        issuer_access = access.compute(session, issuer.account, session.get(Token, issuer.token_id))
        for lid in list_ids:
            row = session.get(List, lid, populate_existing=True)
            section = session.get(Section, row.section_id) if row else None
            if (
                not row
                or row.archived_at
                or not section
                or section.archived_at
                or agent_access.list_level(lid) < access.EDIT
                or issuer_access.list_level(lid) < access.EDIT
            ):
                raise Invalid(
                    "Both issuer and agent need edit access to every active selected list."
                )
    return agent


def _capabilities(value: list[str]) -> list[str]:
    if not isinstance(value, list) or any(x not in CAPABILITIES for x in value):
        raise Invalid("Unsupported connection capability.")
    return sorted(set(value))


def _text(value: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Invalid("Connection metadata is missing or too long.")
    if any(ord(ch) < 32 for ch in value):
        raise Invalid("Connection metadata must be plain text.")
    return value.strip()


def view(session: Session, row: AgentConnection) -> dict:
    """Private lifecycle view, with no credentials, runtime paths or diagnostic output."""
    now = utcnow()
    token = session.get(Token, row.token_id) if row.token_id else None
    agent = session.get(Account, row.account_id)
    if row.revoked_at or (token and token.revoked_at):
        state, detail = "revoked", "Disconnected. This does not prove a remote process stopped."
    elif not agent or agent.disabled_at:
        state, detail = "problem", "Agent account is disabled."
    elif not row.exchanged_at:
        if row.expires_at <= now:
            state, detail = "problem", "Pairing code expired. Create a new connection."
        else:
            state, detail = "waiting", "Run the connection command on your computer."
    elif not token or (token.expires_at and token.expires_at <= now):
        state, detail = "problem", "Connection credential expired or unavailable. Reconnect."
    elif not row.last_seen_at or now - row.last_seen_at > STALE:
        state, detail = "offline", "Connection not seen recently. Run doctor on the agent computer."
    elif row.check in ("needs_setup", "runtime_unavailable"):
        state, detail = "problem", CHECKS[row.check]
    elif row.check == "ready" and row.verified_at:
        state, detail = "ready", CHECKS["ready"]
    else:
        state, detail = "connected", CHECKS["connected"]
    return {
        "id": row.id,
        "account_id": row.account_id,
        "label": row.label,
        "list_ids": row.list_ids,
        "state": state,
        "capabilities": row.capabilities,
        "last_seen_at": row.last_seen_at,
        "verified_at": row.verified_at,
        "detail": detail,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "revoked_at": row.revoked_at,
    }


def _event(cx, row: AgentConnection, verb: str):
    cx.record(
        CONNECTION_EVENTS[verb],
        subject_id=row.account_id,
        after={
            "connection_id": row.id,
            "account_id": row.account_id,
            "state": view(cx.session, row)["state"],
        },
        covers=[row],
    )


def issue(
    session: Session,
    actor: Actor,
    *,
    by: Principal,
    account_id: str,
    label: str,
    list_ids: list[str],
    ttl_seconds: int = 600,
) -> tuple[dict, str]:
    """Create a short-lived pending connection without granting account access."""
    with apply_change(session, actor) as cx:
        if not _manager(session, by):
            raise Forbidden("Only an unrestricted owner or admin can connect an agent.")
        if not 60 <= ttl_seconds <= 900:
            raise Invalid("Pairing lifetime must be 60 to 900 seconds.")
        _agent_scope(session, account_id, list_ids, by)
        code = secrets.token_urlsafe(32)
        row = AgentConnection(
            id=new_id(),
            account_id=account_id,
            issuer_id=by.account.id,
            issuer_token_id=by.token_id,
            label=_text(label, 80),
            list_ids=sorted(set(list_ids)),
            secret_hash=accounts.hash_token(code),
            expires_at=utcnow() + timedelta(seconds=ttl_seconds),
            created_at=utcnow(),
            capabilities=[],
            check="connected",
        )
        session.add(row)
        with session.no_autoflush:
            _event(cx, row, "created")
    return view(session, row), code


def exchange(
    session: Session,
    *,
    code: str,
    device_name: str,
    adapter: str,
    capabilities: list[str],
    version: str,
) -> dict:
    """Consume once atomically; issue a distinct list-limited write token, never an admin."""
    # The actor is the issuer: the bearer of their one-time consent has no account yet.
    # Take the same write lock before the first lookup (not a cached pre-lock ORM read).
    from slate.service.core import _write_lock

    with _write_lock:
        row = session.scalar(
            select(AgentConnection)
            .where(AgentConnection.secret_hash == accounts.hash_token(code))
            .execution_options(populate_existing=True)
        )
        if (
            not row
            or not hmac.compare_digest(row.secret_hash, accounts.hash_token(code))
            or row.exchanged_at
            or row.revoked_at
            or row.expires_at <= utcnow()
        ):
            raise Gone("Pairing code is invalid, expired or already used. Create a new connection.")
        issuer = session.get(Account, row.issuer_id, populate_existing=True)
        by = Principal(issuer, frozenset({"admin"}), row.issuer_token_id)
        actor = Actor(row.issuer_id, Source.api)
        with apply_change(session, actor) as cx:
            try:
                if not _manager(session, by):
                    raise Invalid("Issuer unavailable")
                agent = _agent_scope(session, row.account_id, row.list_ids, by)
            except Invalid:
                raise Gone("Pairing authorization changed. Create a new connection.") from None
            caps = _capabilities(capabilities)
            device_name, adapter, version = (
                _text(device_name, 80),
                _text(adapter, 80),
                _text(version, 40),
            )
            token, plaintext = accounts.create_token(
                session,
                actor,
                account_id=row.account_id,
                scopes=["write"],
                label="Local connection",
                limit={"list_ids": row.list_ids, "level": "edit"},
                by=by,
            )
            row.token_id = token.id
            row.exchanged_at = row.last_seen_at = utcnow()
            row.device_name, row.adapter, row.version = device_name, adapter, version
            row.capabilities = caps
            with session.no_autoflush:
                _event(cx, row, "connected")
        return {
            "connection": view(session, row),
            "token": plaintext,
            "server_protocol": MIN_RUNNER_PROTOCOL,
            "account_handle": agent.handle,
        }


def list_connections(session: Session, *, by: Principal, account_id: str) -> list[dict]:
    """Only the agent itself or an unrestricted manager sees its local connections."""
    if by.account.id != account_id and not _manager(session, by):
        raise Forbidden("Only this account or an unrestricted manager can see connections.")
    return [
        view(session, row)
        for row in session.scalars(
            select(AgentConnection)
            .where(AgentConnection.account_id == account_id)
            .order_by(AgentConnection.created_at, AgentConnection.id)
        )
    ]


def _owned(session: Session, by: Principal, connection_id: str, *, manager: bool = False):
    row = session.get(AgentConnection, connection_id, populate_existing=True)
    if not row:
        raise NotFound("Connection not found.")
    if manager and _manager(session, by):
        return row
    token = session.get(Token, by.token_id, populate_existing=True)
    account = session.get(Account, by.account.id, populate_existing=True)
    if not (
        row.account_id == by.account.id
        and row.token_id == by.token_id
        and token
        and token.account_id == by.account.id
        and not token.revoked_at
        and (not token.expires_at or token.expires_at > utcnow())
        and account
        and not account.disabled_at
    ):
        raise Forbidden("Use this connection's exact live credential.")
    return row


def heartbeat(
    session: Session,
    actor: Actor,
    *,
    by: Principal,
    connection_id: str,
    check: str,
    capabilities: list[str] | None = None,
) -> dict:
    """Record local health, never launch work; readiness is a recent reported executor probe."""
    with apply_change(session, actor) as cx:
        row = _owned(session, by, connection_id)
        if row.revoked_at:
            raise Gone("Connection revoked.")
        caps = row.capabilities if capabilities is None else _capabilities(capabilities)
        if not set(caps) <= set(row.capabilities):
            raise Invalid("Reconnect to add capabilities.")
        if check not in CHECKS or (check in ("ready", "probe_passed") and "probe" not in caps):
            raise Invalid("Ready requires a supported successful executor probe.")
        if check == "ready" and row.verified_at is None:
            raise Invalid("Run the explicit connection test before reporting ready.")
        old_state = view(session, row)["state"]
        normalized = "ready" if check == "probe_passed" else check
        changed = normalized != row.check or caps != row.capabilities
        row.last_seen_at = utcnow()
        if check == "probe_passed":
            row.verified_at = row.last_seen_at
        elif check in ("runtime_unavailable", "needs_setup") or "probe" not in caps:
            row.verified_at = None
        row.check, row.capabilities = normalized, caps
        with session.no_autoflush:
            # An SSE-only board needs bounded liveness notices even when health is
            # unchanged. Do not make every 30-second beat a new history entry.
            last_notice = session.scalar(
                select(Event.at)
                .where(
                    Event.subject_id == row.account_id,
                    Event.type.in_(("agent.connection_connected", "agent.connection_checked")),
                    Event.after_json["connection_id"].as_string() == row.id,
                )
                .order_by(Event.id.desc())
                .limit(1)
            )
            refresh = last_notice is None or row.last_seen_at - last_notice >= timedelta(seconds=60)
            if (
                changed
                or check == "probe_passed"
                or refresh
                or old_state != view(session, row)["state"]
            ):
                _event(cx, row, "checked")
    return view(session, row)


def revoke(session: Session, actor: Actor, *, by: Principal, connection_id: str) -> None:
    """Revoke this credential; does not assert that a remote process has stopped."""
    with apply_change(session, actor) as cx:
        row = _owned(session, by, connection_id, manager=True)
        if row.revoked_at:
            return
        if row.token_id:
            accounts.revoke_token(session, actor, token_id=row.token_id, by=by)
        row.revoked_at = utcnow()
        with session.no_autoflush:
            _event(cx, row, "revoked")
