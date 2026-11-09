"""Bootstrap, /me, accounts and tokens."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictBool, field_validator

from slate import mailer
from slate.api import collaboration
from slate.api.auth import AdminAuth, DbSession, ReadAuth, WriteAuth
from slate.models import Account, Token
from slate.ratelimit import limiter
from slate.service import access, accounts, aiadd
from slate.settings import settings

router = APIRouter(tags=["accounts"])

Scope = Literal["read", "write", "admin"]
Role = Literal["owner", "admin", "member", "guest"]
Level = Literal["view", "comment", "edit"]


class TokenLimit(BaseModel):
    """Narrow a token below its account: only these sections/lists, at most this level."""

    model_config = ConfigDict(extra="forbid")

    section_ids: list[str] = Field(default_factory=list)
    list_ids: list[str] = Field(default_factory=list)
    level: Level | None = None


class MyAccess(BaseModel):
    """What this token reaches (service/access.py). `all: true` = every section and list."""

    all: bool
    level: Level = Field(description="The most this token can do anywhere")
    manage: bool = Field(description="Owner or admin with an unlimited token")
    general: bool = Field(description="The General chat channel")
    limited: bool = Field(default=False, description="The token narrows the account (limit)")
    sections: dict[str, Level | None] = Field(
        default_factory=dict, description="Section id -> level (null: seen through a list)"
    )
    lists: dict[str, Level] = Field(default_factory=dict)


HANDLE_PATTERN = accounts.HANDLE_RE.pattern


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handle: str
    display_name: str
    kind: Literal["person", "agent"]
    role: Role = "member"
    created_at: datetime
    disabled_at: datetime | None
    wake: Literal["push", "pull", "manual"] = Field(
        default="push",
        description="How an agent is woken for a task: push (a runner; Run now), pull (its own "
        "product's scheduler polls Slate; Nudge), manual (a person carries it; Copy brief)",
    )


class Prefs(BaseModel):
    """How the board looks for this account, on every device. Unknown keys are kept."""

    model_config = ConfigDict(extra="allow")

    theme: Literal["auto", "light", "dark"] | None = None
    collapsed: dict[str, dict[str, StrictBool]] | None = Field(
        default=None, description="Per view key: column key -> folded (true) or open (false)"
    )
    last_view: dict[str, str] | None = Field(
        default=None,
        description="Per scope key (`everything`, `section:<id>`, `list:<id>`): the view tab "
        "last open there, shown again when you come back",
    )
    email_to: str | None = Field(
        default=None, description="Where email notifications go (one address; null removes it)"
    )
    email_reasons: list[str] | None = Field(
        default=None,
        description="Inbox reasons emailed when still unseen after 5 minutes (as push_reasons, "
        "without chat); default mention and waiting",
    )
    view_drafts: dict[str, dict] | None = Field(
        default=None,
        description="Per view key: unsaved toolbar changes (layout, sort, filters...) as the "
        "settings that differ from the saved view; they stay until Save or Revert",
    )


class MeOut(BaseModel):
    account: AccountOut
    scopes: list[Scope]
    token_id: str
    prefs: dict = Field(default_factory=dict, description="Board prefs; see PATCH /me/prefs")
    access: MyAccess | None = None
    ai_add: bool = Field(
        default=False, description="AI add is set up (POST /items/ai); the board shows its switch"
    )
    email: bool = Field(
        default=False,
        description="Email notifications are set up (SLATE_SMTP_HOST); the board shows the "
        "Email column in Notifications",
    )
    timezone: str = Field(default="UTC", description="Workspace IANA timezone (SLATE_TIMEZONE)")
    link_schemes: list[str] = Field(
        default=["http", "https"],
        description="URL schemes accepted for an agent's url and thread.url (the badge's Open "
        "link); the board opens no other scheme. Set with SLATE_LINK_SCHEMES",
    )
    session_expires_at: datetime | None = Field(
        default=None,
        description="When this browser session (an accepted invitation's cookie) ends and the "
        "guest needs a new link or an extension; null for bearer tokens",
    )


class AccountIn(BaseModel):
    handle: str = Field(pattern=HANDLE_PATTERN)
    display_name: str = Field(min_length=1, max_length=100)
    kind: Literal["person", "agent"]
    role: Literal["admin", "member", "guest"] = Field(
        default="member", description="guest: sees only what is shared with it"
    )
    wake: Literal["push", "pull", "manual"] = Field(
        default="push",
        description="Agents: push (a runner), pull (its own scheduler polls Slate), manual",
    )


class BootstrapIn(BaseModel):
    handle: str = Field(pattern=HANDLE_PATTERN)
    display_name: str = Field(min_length=1, max_length=100)


class TokenIn(BaseModel):
    scopes: list[Scope] = Field(min_length=1)
    label: str | None = Field(default=None, max_length=100)
    limit: TokenLimit | None = Field(
        default=None, description="Only these sections/lists, at most this level"
    )


class TokenCreatedOut(BaseModel):
    """Includes the plaintext `token`. This is the only response that ever does."""

    id: str
    account_id: str
    scopes: list[Scope]
    label: str | None
    created_at: datetime
    limit: dict | None = None
    token: str


class TokenOut(BaseModel):
    """A token as listed: never the plaintext or its hash."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    scopes: list[Scope]
    label: str | None
    created_at: datetime
    last_used_at: datetime | None = Field(description="Refreshed at most once a minute")
    revoked_at: datetime | None
    limit: dict | None = Field(
        default=None,
        validation_alias=AliasChoices("limit_json", "limit"),
        description="{section_ids, list_ids, level} or null (the account's full access)",
    )

    @field_validator("scopes", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        return v.split() if isinstance(v, str) else v


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    disabled: StrictBool | None = Field(
        default=None, description="true stops every token of the account at once; false undoes"
    )
    wake: Literal["push", "pull", "manual"] | None = Field(
        default=None, description="Agents: how it is woken for a task (push, pull, manual)"
    )


class UsageDay(BaseModel):
    day: str = Field(description="Local (workspace timezone) date, YYYY-MM-DD")
    events: int
    by_source: dict[str, int] = Field(description="Events that day per source (ui, api, mcp...)")


class TypeCount(BaseModel):
    type: str
    count: int


class TokenUsage(TokenOut):
    requests: dict[str, int] = Field(
        description="Requests since the server started, by kind: read, write"
    )


class UsageOut(BaseModel):
    account_id: str
    days: list[UsageDay] = Field(description="Oldest first; days with no events included")
    events_in_window: int
    events_all_time: int
    last_event_at: datetime | None
    top_types: list[TypeCount] = Field(description="Busiest event types in the window")
    tokens: list[TokenUsage] = Field(description="Live tokens with last use and request counts")
    throttled: int = Field(description="Requests refused by the rate limit since server start")
    rate_limit: dict[str, int] = Field(
        description="Per-token limits per minute: reads, writes (0 = off)"
    )


class BootstrapOut(BaseModel):
    account: AccountOut
    token_id: str
    scopes: list[Scope]
    token: str


def _token_out(token: Token, plaintext: str) -> TokenCreatedOut:
    return TokenCreatedOut(
        id=token.id,
        account_id=token.account_id,
        scopes=token.scopes.split(),
        label=token.label,
        created_at=token.created_at,
        limit=token.limit_json,
        token=plaintext,
    )


def usage_view(session, account_id: str, days: int) -> dict:
    """`accounts.usage()` plus the in-process counters (requests per token, throttled)."""
    out = accounts.usage(session, account_id, days=days)
    out["tokens"] = [
        {**TokenOut.model_validate(t).model_dump(), "requests": limiter.counts(t.id)}
        for t in out["tokens"]
    ]
    out["throttled"] = limiter.throttled.get(account_id, 0)
    on = settings.rate_limit_enabled
    out["rate_limit"] = {
        "reads": settings.rate_limit_reads_per_minute if on else 0,
        "writes": settings.rate_limit_writes_per_minute if on else 0,
    }
    return out


def _ordered(scopes: frozenset[str]) -> list[str]:
    return sorted(scopes, key=accounts.SCOPE_RANK.__getitem__)


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED, response_model=BootstrapOut)
def bootstrap(body: BootstrapIn, session: DbSession) -> BootstrapOut:
    """First run only (no accounts yet): create the first person with an admin token.

    Unauthenticated. Returns 410 Gone once any account exists.
    """
    account, token, plaintext = accounts.bootstrap(
        session, handle=body.handle, display_name=body.display_name
    )
    return BootstrapOut(
        account=AccountOut.model_validate(account),
        token_id=token.id,
        scopes=token.scopes.split(),
        token=plaintext,
    )


def my_access(session, account: Account) -> MyAccess:
    found = access.current(session) or access.Access(account.id, account.role)
    level = access.LEVEL_NAME[found.cap]
    if found.lists is None:
        return MyAccess(
            all=True, level=level, manage=found.is_admin, general=True, limited=found.limited
        )
    return MyAccess(
        all=False,
        level=level,
        manage=False,
        general=found.general,
        limited=found.limited,
        sections={sid: access.LEVEL_NAME[lvl] for sid, lvl in (found.sections or {}).items()},
        lists={lid: access.LEVEL_NAME[lvl] for lid, lvl in found.lists.items()},
    )


def me_view(session, principal: accounts.Principal) -> MeOut:
    """One self-metadata contract for REST and local MCP."""
    token = session.get(Token, principal.token_id)
    return MeOut(
        account=AccountOut.model_validate(principal.account),
        scopes=_ordered(principal.scopes),
        token_id=principal.token_id,
        prefs=accounts.get_prefs(principal.account),
        access=my_access(session, principal.account),
        ai_add=aiadd.enabled(),
        email=mailer.enabled(),
        link_schemes=list(settings.link_schemes),
        timezone=settings.timezone,
        session_expires_at=token.expires_at if token and token.browser_session else None,
    )


@router.get("/me", response_model=MeOut)
def me(auth: ReadAuth, session: DbSession, request: Request, response: Response) -> MeOut:
    """Who this token is, its scopes, board prefs and `access`: the role's reach through this
    token (every list, or the sections/lists it can see with their level). The board hides
    what `access` rules out; the server enforces it regardless. A browser session also gets
    its cookie's lifetime renewed here (`session_expires_at` says when the session itself
    ends)."""
    out = me_view(session, auth.principal)
    cookie = request.cookies.get(collaboration.COOKIE)
    if out.session_expires_at is not None and cookie and not request.headers.get("authorization"):
        collaboration.set_session_cookie(response, request, cookie)
        response.headers["Cache-Control"] = "no-store"
    return out


@router.patch("/me/prefs", response_model=dict)
def set_my_prefs(body: Prefs, auth: WriteAuth, session: DbSession) -> dict:
    """Merge into your board prefs (a key sent as null is removed) and return them all. The
    change reaches your other devices live as `account.prefs_changed`."""
    changes = body.model_dump(exclude_unset=True)
    return accounts.set_prefs(session, auth.actor, changes)


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(_: AdminAuth, session: DbSession) -> list[Account]:
    return accounts.list_accounts(session)


@router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=AccountOut)
def create_account(
    body: AccountIn,
    auth: AdminAuth,
    session: DbSession,
) -> Account:
    """Create a person or agent. `role` defaults to member; a guest sees only what is shared
    with it; only the owner creates admins."""
    return accounts.create_account(
        session,
        auth.actor,
        handle=body.handle,
        display_name=body.display_name,
        kind=body.kind,
        role=body.role,
        wake=body.wake,
    )


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: str, body: AccountPatch, auth: AdminAuth, session: DbSession
) -> Account:
    """Rename an account, or disable it (`disabled: true`): its tokens stop working at once,
    it leaves the roster, its history and assignments stay. `disabled: false` re-enables it.
    You cannot disable yourself. `wake` sets how an agent is woken: `push` (a runner), `pull`
    (its own product polls; Run now nudges its owner) or `manual` (Copy brief).
    The `slate` account is Slate's own (due dates, reminders and automations are recorded as
    it, source `system`) and cannot be changed: 422."""
    return accounts.update_account(
        session,
        auth.actor,
        account_id=account_id,
        by=auth.principal,
        **body.model_dump(exclude_unset=True),
    )


@router.get("/accounts/{account_id}/tokens", response_model=list[TokenOut])
def list_tokens(
    account_id: str, auth: ReadAuth, session: DbSession, include_revoked: bool = False
) -> list[Token]:
    """An account's tokens, newest first, with `last_used_at` (never the secret). Admins can
    list anyone's; any account can list its own."""
    accounts.require_self_or_admin(auth.principal, account_id)
    return accounts.list_tokens(session, account_id, include_revoked=include_revoked)


@router.get("/accounts/{account_id}/usage", response_model=UsageOut)
def account_usage(
    account_id: str,
    auth: ReadAuth,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> dict:
    """What an account has been doing: events per day by source (`ui`, `api`, `mcp`,
    `system`), its busiest event types, and each live token's last use and request counts.
    Request counts and `throttled` are since the server last started. Admin, or yourself."""
    accounts.require_self_or_admin(auth.principal, account_id)
    return usage_view(session, account_id, days)


@router.post(
    "/accounts/{account_id}/tokens",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenCreatedOut,
)
def create_token(
    account_id: str,
    body: TokenIn,
    auth: AdminAuth,
    session: DbSession,
) -> TokenCreatedOut:
    """Issue a token for an account. The plaintext is in this response and nowhere else.
    `limit` narrows it to some sections/lists and a level (an agent that may only touch one
    list); a token never reaches further than its account's role and shares."""
    token, plaintext = accounts.create_token(
        session,
        auth.actor,
        account_id=account_id,
        scopes=list(body.scopes),
        label=body.label,
        limit=body.limit.model_dump(exclude_defaults=True) if body.limit else None,
        by=auth.principal,
    )
    return _token_out(token, plaintext)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    token_id: str,
    auth: ReadAuth,
    session: DbSession,
) -> Response:
    """Revoke a token. Admins can revoke any token; any account can revoke its own."""
    accounts.revoke_token(session, auth.actor, token_id=token_id, by=auth.principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
