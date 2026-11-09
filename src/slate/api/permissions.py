"""Members, roles, sharing and the audit log (service/permissions.py, service/audit.py).

GET  /members                      every account with role, shares, last activity (admin)
PUT  /accounts/{id}/role           owner|admin|member|guest (admin; admins only by owner)
GET  /accounts/{id}/access         what an account reaches (admin, or yourself)
GET  /shares                       ?account_id=&scope_kind=&scope_id= (admin)
PUT  /shares                       grant/change/revoke one account on a section or list
DELETE /shares/{id}
GET  /sections/{id}/access, /lists/{id}/access   who reaches it, and how (admin)
GET  /audit                        the event log, newest first, filterable
GET  /audit/facets                 types, actors and sections for the filter menus
GET  /audit.csv                    the same filters as a CSV download
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from slate.api.accounts import AccountOut
from slate.api.auth import AdminAuth, DbSession, ReadAuth
from slate.service import accounts, audit, permissions

router = APIRouter(tags=["permissions"])

RoleName = Literal["owner", "admin", "member", "guest"]
Level = Literal["view", "comment", "edit"]
ScopeKind = Literal["section", "list"]


class MemberOut(BaseModel):
    account: AccountOut
    role: RoleName
    shares: int = Field(description="How many sections/lists are shared with this account")
    last_active_at: datetime | None


class RoleIn(BaseModel):
    role: RoleName = Field(
        description="owner hands ownership over (you become admin); only the owner sets admins"
    )


class ShareIn(BaseModel):
    account_id: str
    scope_kind: ScopeKind
    scope_id: str
    level: Level | None = Field(description="null revokes the share")


class ShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    scope_kind: ScopeKind
    scope_id: str
    level: Level
    created_by: str
    created_at: datetime


class AccessRow(BaseModel):
    account_id: str
    level: Level
    via: Literal["role", "section", "share"] = Field(
        description="role: owner/admin or a member's default; section: inherited; share: direct"
    )
    share_id: str | None


class AccountAccessOut(BaseModel):
    account_id: str
    role: RoleName
    all: bool = Field(description="true: every section and list (owner, admin, or no privacy)")
    sections: dict[str, Level]
    lists: dict[str, Level]


class AuditActor(BaseModel):
    id: str
    handle: str | None
    display_name: str | None
    kind: Literal["person", "agent"] | None


class AuditItem(BaseModel):
    id: str
    title: str
    list_id: str
    list_name: str | None


class AuditEntry(BaseModel):
    id: str
    at: datetime
    type: str
    field: str | None
    before: Any
    after: Any
    source: str
    session_ref: str | None
    actor: AuditActor
    item_id: str | None
    subject_id: str | None
    item: AuditItem | None
    summary: str


class AuditPage(BaseModel):
    entries: list[AuditEntry]
    next_before: str | None = Field(description="Pass back as `before` for older entries")
    has_more: bool


class AuditFacets(BaseModel):
    types: list[str]
    actors: list[AuditActor]
    sections: list[dict[str, str]]


@router.get("/members", response_model=list[MemberOut])
def list_members(_: AdminAuth, session: DbSession) -> list[dict]:
    """Every account (people and agents) with its role, how many shares it holds and when it
    last did anything. The Members page."""
    return permissions.members(session)


@router.put("/accounts/{account_id}/role", response_model=AccountOut)
def set_role(account_id: str, body: RoleIn, auth: AdminAuth, session: DbSession) -> Any:
    """Change an account's role. Members see everything not private; guests only what is
    shared with them. Only the owner makes or unmakes admins; `owner` hands ownership over."""
    return permissions.set_role(
        session, auth.actor, by=auth.principal, account_id=account_id, role=body.role
    )


@router.get("/accounts/{account_id}/access", response_model=AccountAccessOut)
def account_access(account_id: str, auth: ReadAuth, session: DbSession) -> dict:
    """What an account reaches with an unlimited token: every section and list with its
    level. Admin, or yourself."""
    accounts.require_self_or_admin(auth.principal, account_id)
    return permissions.effective(session, account_id)


@router.get("/shares", response_model=list[ShareOut])
def list_shares(
    _: AdminAuth,
    session: DbSession,
    account_id: str | None = None,
    scope_kind: ScopeKind | None = None,
    scope_id: str | None = None,
) -> list[Any]:
    return permissions.list_shares(
        session, account_id=account_id, scope_kind=scope_kind, scope_id=scope_id
    )


@router.put("/shares", response_model=ShareOut | None)
def put_share(body: ShareIn, auth: AdminAuth, session: DbSession) -> Any:
    """Share a section or list with an account at `view`, `comment` or `edit` (or change the
    level; `level: null` revokes). Returns the share, or null when revoked."""
    return permissions.share(
        session,
        auth.actor,
        by=auth.principal,
        account_id=body.account_id,
        scope_kind=body.scope_kind,
        scope_id=body.scope_id,
        level=body.level,
    )


@router.delete("/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_share(share_id: str, auth: AdminAuth, session: DbSession) -> Response:
    permissions.revoke_share(session, auth.actor, by=auth.principal, share_id=share_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sections/{section_id}/access", response_model=list[AccessRow])
def section_access(section_id: str, _: AdminAuth, session: DbSession) -> list[dict]:
    """Everyone who reaches this section, at what level and why (the sharing dialog)."""
    return permissions.who_can_see(session, "section", section_id)


@router.get("/lists/{list_id}/access", response_model=list[AccessRow])
def list_access(list_id: str, _: AdminAuth, session: DbSession) -> list[dict]:
    """Everyone who reaches this list, at what level and why (the sharing dialog)."""
    return permissions.who_can_see(session, "list", list_id)


def _filter(
    actor: str | None,
    actor_kind: str | None,
    source: str | None,
    type: str | None,
    item_id: str | None,
    list_id: str | None,
    section_id: str | None,
    since: datetime | None,
    until: datetime | None,
    q: str | None,
) -> audit.AuditFilter:
    types = tuple(t.strip() for t in (type or "").split(",") if t.strip())
    return audit.AuditFilter(
        actor=actor,
        actor_kind=actor_kind,
        source=source,
        types=types,
        item_id=item_id,
        list_id=list_id,
        section_id=section_id,
        since=since,
        until=until,
        q=q,
    )


ActorQ = Annotated[str | None, Query(max_length=32, description="Handle or account id")]
KindQ = Annotated[Literal["person", "agent"] | None, Query()]
SourceQ = Annotated[Literal["ui", "api", "mcp", "system"] | None, Query()]
TypeQ = Annotated[
    str | None,
    Query(description="Comma-separated types; a trailing dot is a prefix (e.g. `share.`)"),
]
IdQ = Annotated[str | None, Query(max_length=12)]
TextQ = Annotated[
    str | None, Query(max_length=200, description="Text in the task title or session_ref")
]


@router.get("/audit", response_model=AuditPage)
def audit_log(
    _: ReadAuth,
    session: DbSession,
    actor: ActorQ = None,
    actor_kind: KindQ = None,
    source: SourceQ = None,
    type: TypeQ = None,
    item_id: IdQ = None,
    list_id: IdQ = None,
    section_id: IdQ = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: TextQ = None,
    before: Annotated[str | None, Query(max_length=12, description="Event id cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """Who (person or agent) did what, when and through which door, newest first. Read-only.
    Owners and admins see everything; anyone else sees the lists they can read and their own
    actions."""
    f = _filter(actor, actor_kind, source, type, item_id, list_id, section_id, since, until, q)
    return audit.page(session, f, before=before, limit=limit)


@router.get("/audit/facets", response_model=AuditFacets)
def audit_facets(_: ReadAuth, session: DbSession) -> dict:
    """Event types and actors that appear in the log you can see, for filter menus."""
    return audit.facets(session)


@router.get(
    "/audit.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV download"}},
)
def audit_csv(
    _: ReadAuth,
    session: DbSession,
    actor: ActorQ = None,
    actor_kind: KindQ = None,
    source: SourceQ = None,
    type: TypeQ = None,
    item_id: IdQ = None,
    list_id: IdQ = None,
    section_id: IdQ = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: TextQ = None,
) -> Response:
    """The audit log with the same filters, as CSV (newest first, up to 50,000 rows)."""
    f = _filter(actor, actor_kind, source, type, item_id, list_id, section_id, since, until, q)
    return Response(
        audit.export_csv(session, f),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="slate-audit.csv"'},
    )
