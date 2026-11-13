"""Accounts and bearer tokens.

Token format: `slate_<token id>_<secret>`. The token id is a normal 12-char base62 id (no `_`),
so the plaintext splits unambiguously. Only `sha256(plaintext)` is stored; the plaintext is
returned once, by `create_token()` or `bootstrap()`, and never again.

Scopes are ranked: `admin` includes `write`, which includes `read`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.dates import LOCAL
from slate.ids import new_id
from slate.models import SYSTEM_HANDLE, Account, AccountKind, Event, Role, Source, Token, utcnow
from slate.service.core import Actor, apply_change
from slate.service.errors import Conflict, Forbidden, Gone, Invalid, NotFound

SCOPE_RANK = {"read": 1, "write": 2, "admin": 3}
HANDLE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
TOKEN_RE = re.compile(r"^slate_([0-9A-Za-z]{12})_([A-Za-z0-9_-]{20,})$")
THEMES = ("auto", "light", "dark")
# Inbox reasons a person can choose to get as phone/desktop notifications (slate.pusher).
PUSH_REASONS = (
    "mention",
    "reply",
    "chat",
    "assigned",
    "waiting",
    "unblocked",
    "nudge",
    "due",
    "reminder",
    "digest",
    "health",
    "access",
    "comment",
    "status",
    "update",
    "archived",
    "moved",
    "watching",
)
# Inbox reasons a person can choose to get by email (service/email.py). Not `chat`: a busy
# channel is a toast, never an email.
EMAIL_REASONS = tuple(r for r in PUSH_REASONS if r != "chat")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
# What auto-watch can follow (service/watchers.py; kept here so prefs need no import cycle).
AUTO_WATCH = ("created", "assigned", "commented")
# Room for the remembered views (last tab and unsaved toolbar changes per scope) besides the rest.
PREFS_MAX_BYTES = 32_768
# last_used_at is bookkeeping; refresh it at most this often to avoid a write per request.
LAST_USED_RESOLUTION = timedelta(seconds=60)


@dataclass(frozen=True)
class Principal:
    """An authenticated caller: the account, the token's scopes, and the token id."""

    account: Account
    scopes: frozenset[str]
    token_id: str

    def has(self, scope: str) -> bool:
        return has_scope(self.scopes, scope)

    @property
    def is_admin(self) -> bool:
        """`access.grants_admin`: `_lookup` already drops `admin` from a limited token."""
        from slate.service.access import grants_admin

        return grants_admin(self.account.role, self.scopes)


def has_scope(held: frozenset[str] | set[str], required: str) -> bool:
    return max((SCOPE_RANK[s] for s in held), default=0) >= SCOPE_RANK[required]


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _normalize_scopes(scopes: list[str] | tuple[str, ...] | set[str]) -> str:
    unknown = set(scopes) - SCOPE_RANK.keys()
    if unknown or not scopes:
        raise Invalid(f"scopes must be a non-empty subset of {sorted(SCOPE_RANK)}")
    return " ".join(sorted(set(scopes), key=SCOPE_RANK.__getitem__))


def _validate_account(handle: str, display_name: str, kind: str) -> AccountKind:
    if not HANDLE_RE.match(handle):
        raise Invalid("handle must be 2-32 chars: lowercase letter, then a-z 0-9 _ -")
    if handle == SYSTEM_HANDLE:
        raise Invalid(f"`{SYSTEM_HANDLE}` is Slate's own account; choose another handle")
    if not display_name.strip():
        raise Invalid("display_name is required")
    try:
        return AccountKind(kind)
    except ValueError:
        raise Invalid("kind must be 'person' or 'agent'") from None


# --- Reads ---------------------------------------------------------------------------------


def list_accounts(session: Session) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.handle)))


def get_account(session: Session, account_id: str) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise NotFound(f"account {account_id} not found")
    return account


def list_tokens(session: Session, account_id: str, *, include_revoked: bool = False) -> list[Token]:
    """An account's tokens, newest first. Never the hash: callers show `id`, `label`, `scopes`,
    `created_at`, `last_used_at`, `revoked_at`."""
    get_account(session, account_id)
    q = select(Token).where(Token.account_id == account_id)
    caller_id = session.info.get("slate_authenticated_token_id")
    caller = session.get(Token, caller_id) if caller_id else None
    if caller is not None and caller.limit_json:
        q = q.where(Token.id == caller.id)
    if not include_revoked:
        q = q.where(Token.revoked_at.is_(None))
    return list(session.scalars(q.order_by(Token.created_at.desc(), Token.id.desc())))


def require_self_or_admin(by: Principal, account_id: str) -> None:
    if not by.is_admin and by.account.id != account_id:
        raise Forbidden("only an admin or the account itself can see this")


def usage(
    session: Session, account_id: str, *, days: int = 14, now: datetime | None = None
) -> dict[str, Any]:
    """What an account has been doing: events per workspace-local day (oldest first, zero days
    included) split by source, the busiest event types, totals, and its live tokens with
    `last_used_at`. Request counts and rate-limit refusals are in-process counters the router
    adds (`slate.ratelimit`)."""
    account = get_account(session, account_id)
    now = now or utcnow()
    today = now.astimezone(LOCAL).date()
    first = today - timedelta(days=days - 1)
    start = datetime.combine(first, time.min, tzinfo=LOCAL).astimezone(UTC)
    by_day: dict[str, dict[str, int]] = {
        (first + timedelta(days=i)).isoformat(): {} for i in range(days)
    }
    types: dict[str, int] = {}
    rows = session.execute(
        select(Event.at, Event.source, Event.type).where(
            Event.actor_id == account.id, Event.at >= start
        )
    )
    total = 0
    for at, source, type_ in rows:
        day = at.astimezone(LOCAL).date().isoformat()
        src = source.value if hasattr(source, "value") else str(source)
        bucket = by_day.setdefault(day, {})
        bucket[src] = bucket.get(src, 0) + 1
        types[type_] = types.get(type_, 0) + 1
        total += 1
    all_time = (
        session.scalar(select(func.count()).select_from(Event).where(Event.actor_id == account.id))
        or 0
    )
    last_event = session.scalar(select(func.max(Event.at)).where(Event.actor_id == account.id))
    return {
        "account_id": account.id,
        "days": [{"day": d, "events": sum(v.values()), "by_source": v} for d, v in by_day.items()],
        "events_in_window": total,
        "events_all_time": all_time,
        "last_event_at": last_event,
        "top_types": [
            {"type": t, "count": c}
            for t, c in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        ],
        "tokens": list_tokens(session, account.id),
    }


def account_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Account)) or 0


def _bootstrapped(session: Session) -> bool:
    """Someone besides Slate's own account exists (which a scheduler alarm can create first)."""
    return (
        session.scalar(select(Account.id).where(Account.handle != SYSTEM_HANDLE).limit(1))
        is not None
    )


def resolve_token(session: Session, plaintext: str, *, touch: bool = True) -> Principal | None:
    """Bearer token -> Principal, or None if malformed, unknown, revoked or disabled.

    Updates `last_used_at` (bookkeeping, no event; see `core.BOOKKEEPING`) at most once a
    minute. `touch=False` only reads; the caller then calls `touch_token()` when
    `needs_touch()` says so (the async REST auth does that write off the event loop).
    """
    principal = _lookup(session, plaintext)
    if principal is not None and touch and needs_touch(session, principal.token_id):
        touch_token(session, principal.token_id)
    return principal


def authenticate(
    session: Session, plaintext: str, *, cookie: bool = False, touch: bool = True
) -> Principal | None:
    """The one token check every door uses (REST, the live stream, MCP): `resolve_token`, and
    a credential read from the browser cookie must be a browser session, not a pasted API
    token. The caller then checks the scope it needs and binds the caller's access."""
    principal = resolve_token(session, (plaintext or "").strip(), touch=touch)
    if principal is not None and cookie:
        token = session.get(Token, principal.token_id)
        if token is None or not token.browser_session:
            return None
    return principal


def needs_touch(session: Session, token_id: str) -> bool:
    token = session.get(Token, token_id)
    return token is not None and (
        token.last_used_at is None or utcnow() - token.last_used_at >= LAST_USED_RESOLUTION
    )


def touch_token(session: Session, token_id: str) -> None:
    """Stamp `last_used_at` now (bookkeeping: no event)."""
    token = session.get(Token, token_id)
    if token is not None:
        token.last_used_at = utcnow()
        session.commit()


def _lookup(session: Session, plaintext: str) -> Principal | None:
    match = TOKEN_RE.match(plaintext or "")
    if not match:
        return None
    token = session.get(Token, match.group(1))
    if (
        token is None
        or token.revoked_at is not None
        or (token.expires_at and token.expires_at <= utcnow())
    ):
        return None
    if not hmac.compare_digest(token.token_hash, hash_token(plaintext)):
        return None
    account = session.get(Account, token.account_id)
    if account is None or account.disabled_at is not None:
        return None
    scopes = set(token.scopes.split())
    if account.role not in (Role.owner, Role.admin) or token.limit_json:
        # The token's scope never outranks the account's role: admin needs an admin role,
        # and a list-limited token is never an admin token.
        scopes.discard("admin")
        if not scopes:
            scopes = {"write"}
    return Principal(account=account, scopes=frozenset(scopes), token_id=token.id)


# --- Writes --------------------------------------------------------------------------------


WAKE_MODES = ("push", "pull", "manual")


def _wake(value: str) -> str:
    if value not in WAKE_MODES:
        raise Invalid("wake must be push, pull or manual")
    return value


def create_account(
    session: Session,
    actor: Actor,
    *,
    handle: str,
    display_name: str,
    kind: str,
    role: str = "member",
    wake: str = "push",
) -> Account:
    """Create an account. `role` is member by default; `guest` sees only what is shared with
    it (service/permissions.py). Only the owner creates admins; nobody creates a second owner."""
    account_kind = _validate_account(handle, display_name, kind)
    try:
        account_role = Role(role)
    except ValueError:
        raise Invalid("role must be admin, member or guest") from None
    if account_role == Role.owner:
        raise Invalid("there is one owner; hand ownership over from the Members page instead")
    if account_role == Role.admin:
        creator = session.get(Account, actor.account_id)
        if creator is None or creator.role != Role.owner:
            raise Forbidden("only the owner can create admins")
    if session.scalar(select(Account.id).where(Account.handle == handle)):
        raise Conflict(f"handle '{handle}' is taken")
    with apply_change(session, actor) as cx:
        return cx.create(
            Account(
                handle=handle,
                display_name=display_name.strip(),
                kind=account_kind,
                role=account_role,
                wake=_wake(wake),
            ),
            "account.created",
        )


def _normalize_limit(session: Session, limit: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a token limit `{section_ids?, list_ids?, level?}`; None or empty = no limit."""
    if not limit:
        return None
    from slate.models import List, Section

    unknown = set(limit) - {"section_ids", "list_ids", "level"}
    if unknown:
        raise Invalid(f"unknown token limit keys: {sorted(unknown)}")
    out: dict[str, Any] = {}
    for key, model in (("section_ids", Section), ("list_ids", List)):
        ids = limit.get(key) or []
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise Invalid(f"{key} must be a list of ids")
        for i in ids:
            if session.get(model, i) is None:
                raise Invalid(f"{key}: {i} not found")
        if ids:
            out[key] = sorted(set(ids))
    level = limit.get("level")
    if level is not None:
        if level not in ("view", "comment", "edit"):
            raise Invalid("level must be view, comment or edit")
        out["level"] = level
    return out or None


def create_token(
    session: Session,
    actor: Actor,
    *,
    account_id: str,
    scopes: list[str],
    label: str | None = None,
    limit: dict[str, Any] | None = None,
    by: Principal | None = None,
) -> tuple[Token, str]:
    """Issue a token. Returns (token row, plaintext); the plaintext is not recoverable later.

    `limit` narrows the token below its account's access (service/access.py):
    `{"list_ids": [...], "section_ids": [...], "level": "view|comment|edit"}`. A limited token
    is never an admin token. `by` is the caller: only the owner mints tokens for the owner
    account (an admin holding an owner token would be the owner)."""
    scope_str = _normalize_scopes(scopes)
    account = get_account(session, account_id)
    if account.handle == SYSTEM_HANDLE:
        raise Invalid("`slate` is Slate's own account: it takes no tokens")
    if by is not None:
        _require_outranks(by, account, "issue tokens for")
    if account.disabled_at is not None:
        raise Invalid("account is disabled")
    limit_json = _normalize_limit(session, limit)
    if "admin" in scope_str.split() and (
        limit_json or account.role not in (Role.owner, Role.admin)
    ):
        # Kept for old callers: an admin scope on a member account works as write.
        scope_str = _normalize_scopes([s for s in scope_str.split() if s != "admin"] or ["write"])
    token_id = new_id()
    plaintext = f"slate_{token_id}_{secrets.token_urlsafe(32)}"
    token = Token(
        id=token_id,
        account_id=account.id,
        token_hash=hash_token(plaintext),
        scopes=scope_str,
        label=label,
        limit_json=limit_json,
    )
    with apply_change(session, actor) as cx:
        cx.create(token, "token.created")
    return token, plaintext


def _require_outranks(by: Principal, account: Account, what: str) -> None:
    """The owner outranks admins: only the owner (or the account itself) may `what` an admin
    or the owner account. Otherwise one admin could act as another (a token on its account),
    lock it out, or do the same to the owner."""
    if by.account.id == account.id or account.role not in (Role.owner, Role.admin):
        return
    if account.role == Role.owner or by.account.role != Role.owner:
        raise Forbidden(f"only the owner can {what} an admin or the owner account")


def revoke_token(session: Session, actor: Actor, *, token_id: str, by: Principal) -> Token:
    """Revoke a token. Allowed to an admin, or to the token's own account. Idempotent. The
    owner's tokens are revoked only by the owner."""
    token = session.get(Token, token_id)
    if token is None:
        raise NotFound(f"token {token_id} not found")
    if not by.is_admin and token.account_id != by.account.id:
        raise Forbidden("only an admin or the token's own account can revoke it")
    caller = session.get(Token, by.token_id)
    if caller is not None and caller.limit_json and token.id != caller.id:
        raise Forbidden("a scoped credential can revoke only itself")
    _require_outranks(by, get_account(session, token.account_id), "revoke tokens of")
    if token.revoked_at is None:
        with apply_change(session, actor) as cx:
            cx.set(token, "revoked_at", utcnow(), "token.revoked")
    return token


def update_account(
    session: Session,
    actor: Actor,
    *,
    account_id: str,
    by: Principal,
    display_name: str | None = None,
    disabled: bool | None = None,
    wake: str | None = None,
) -> Account:
    """Rename or disable/enable an account, or set an agent's `wake` mode (admin). A disabled
    account's tokens stop working at once and it drops off the roster; its history and
    assignments stay. You cannot disable your own account (that is how an admin locks
    themselves out)."""
    if not by.is_admin:
        raise Forbidden("only an admin can change an account")
    account = get_account(session, account_id)
    if account.handle == SYSTEM_HANDLE:
        raise Invalid(
            "`slate` is Slate's own account: due dates, reminders and automations are recorded "
            "as it. It cannot be renamed, disabled or woken."
        )
    if display_name is not None and not display_name.strip():
        raise Invalid("display_name is required")
    if disabled and account.id == by.account.id:
        raise Invalid("you cannot disable your own account")
    _require_outranks(by, account, "rename, disable or enable")
    with apply_change(session, actor) as cx:
        if display_name is not None:
            cx.set(account, "display_name", display_name.strip(), "account.updated")
        if wake is not None:
            cx.set(account, "wake", _wake(wake), "account.updated")
        if disabled is True and account.disabled_at is None:
            cx.set(account, "disabled_at", utcnow(), "account.disabled")
        elif disabled is False and account.disabled_at is not None:
            cx.set(account, "disabled_at", None, "account.enabled")
    return account


def bootstrap(session: Session, *, handle: str, display_name: str) -> tuple[Account, Token, str]:
    """First run only: create the first person account with an admin token.

    The new account is the actor of its own creation events (there is no one else yet).
    Raises `Gone` once any account exists.
    """
    _validate_account(handle, display_name, "person")
    if _bootstrapped(session):
        raise Gone("Slate is already bootstrapped")
    account = Account(
        id=new_id(),
        handle=handle,
        display_name=display_name.strip(),
        kind=AccountKind.person,
        role=Role.owner,
    )
    actor = Actor(account_id=account.id, source=Source.api)
    with apply_change(session, actor) as cx:
        # Checked again under the write lock: two first requests at once must not both win.
        if _bootstrapped(session):
            raise Gone("Slate is already bootstrapped")
        cx.create(account, "account.created")
        token, plaintext = create_token(
            session, actor, account_id=account.id, scopes=["admin"], label="bootstrap"
        )
    from slate.service import views

    views.seed_default_views(session, actor)
    return account, token, plaintext


def get_prefs(account: Account) -> dict[str, Any]:
    return dict(account.prefs_json or {})


def _validate_prefs(prefs: dict[str, Any]) -> None:
    theme = prefs.get("theme")
    if theme is not None and theme not in THEMES:
        raise Invalid(f"theme must be one of {list(THEMES)}")
    collapsed = prefs.get("collapsed")
    if collapsed is not None:
        ok = isinstance(collapsed, dict) and all(
            isinstance(cols, dict) and all(isinstance(v, bool) for v in cols.values())
            for cols in collapsed.values()
        )
        if not ok:
            raise Invalid("collapsed must map a view key to {column key: true|false}")
    digest = prefs.get("digest")
    if digest is not None and not isinstance(digest, bool):
        raise Invalid("digest must be true or false")
    hour = prefs.get("digest_hour")
    if hour is not None and (
        isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23
    ):
        raise Invalid("digest_hour must be a whole hour, 0 to 23 (workspace time)")
    push = prefs.get("push_reasons")
    if push is not None and not (
        isinstance(push, list) and all(isinstance(r, str) and r in PUSH_REASONS for r in push)
    ):
        raise Invalid(f"push_reasons must be a list of {', '.join(PUSH_REASONS)}")
    mail = prefs.get("email_reasons")
    if mail is not None and not (
        isinstance(mail, list) and all(isinstance(r, str) and r in EMAIL_REASONS for r in mail)
    ):
        raise Invalid(f"email_reasons must be a list of {', '.join(EMAIL_REASONS)}")
    to = prefs.get("email_to")
    if to is not None and not (isinstance(to, str) and len(to) <= 254 and EMAIL_RE.match(to)):
        raise Invalid("email_to must be one email address")
    renudge = prefs.get("chat_renudge_minutes")
    if renudge is not None and (
        isinstance(renudge, bool) or not isinstance(renudge, int) or not 0 <= renudge <= 24 * 60
    ):
        raise Invalid("chat_renudge_minutes must be a whole number of minutes, 0 (off) to 1440")
    auto = prefs.get("auto_watch")
    if auto is not None and not (
        isinstance(auto, dict)
        and all(k in AUTO_WATCH and isinstance(v, bool) for k, v in auto.items())
    ):
        raise Invalid(f"auto_watch must map {', '.join(AUTO_WATCH)} to true or false")
    last_view = prefs.get("last_view")
    if last_view is not None and not (
        isinstance(last_view, dict) and all(isinstance(v, str) for v in last_view.values())
    ):
        raise Invalid("last_view must map a scope key to a view id")
    view_drafts = prefs.get("view_drafts")
    if view_drafts is not None and not (
        isinstance(view_drafts, dict) and all(isinstance(v, dict) for v in view_drafts.values())
    ):
        raise Invalid("view_drafts must map a view key to the changed view settings")
    if len(json.dumps(prefs)) > PREFS_MAX_BYTES:
        raise Invalid(f"prefs are limited to {PREFS_MAX_BYTES} bytes")


def set_prefs(session: Session, actor: Actor, changes: dict[str, Any]) -> dict[str, Any]:
    """Merge `changes` into the actor's own board prefs; a key set to None is removed.

    Prefs follow the account, not the device: the board reads them from /me and the live
    `account.prefs_changed` event, so a theme or a folded column shows the same everywhere.
    """
    account = get_account(session, actor.account_id)
    prefs = get_prefs(account)
    for key, value in changes.items():
        if value is None:
            prefs.pop(key, None)
        else:
            prefs[key] = value
    _validate_prefs(prefs)
    with apply_change(session, actor) as cx:
        cx.set(account, "prefs_json", prefs, "account.prefs_changed")
    return prefs
