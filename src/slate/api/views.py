"""/views: saved lenses over items (docs/views.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.service import views

router = APIRouter(tags=["views"])

ScopeKind = Literal["everything", "section", "list"]


class ViewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    scope_kind: ScopeKind
    scope_id: str | None = None
    config: dict[str, Any]
    position: int | None = None
    shared: bool = True


class ViewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, Any] | None = None
    position: int | None = None
    shared: bool | None = None


class ViewOut(BaseModel):
    id: str
    name: str
    scope_kind: ScopeKind
    scope_id: str | None
    config: dict[str, Any]
    position: int
    owner_id: str
    shared: bool
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@router.get("/views", response_model=list[ViewOut])
def list_views(
    _: ReadAuth,
    session: DbSession,
    scope: str | None = None,
    include_archived: bool = False,
) -> list[dict]:
    """`scope` is `everything`, `section:<id>` or `list:<id>`; omit it for every view."""
    kind, scope_id = _parse_scope(scope)
    found = views.list_views(
        session, scope_kind=kind, scope_id=scope_id, include_archived=include_archived
    )
    return [views.view_out(v) for v in found]


@router.get("/views/{view_id}", response_model=ViewOut)
def get_view(view_id: str, _: ReadAuth, session: DbSession) -> dict:
    return views.view_out(views.get_view(session, view_id))


@router.post("/views", status_code=status.HTTP_201_CREATED, response_model=ViewOut)
def create_view(body: ViewIn, auth: WriteAuth, session: DbSession) -> dict:
    view = views.create_view(session, auth.actor, **body.model_dump())
    return views.view_out(view)


@router.patch("/views/{view_id}", response_model=ViewOut)
def update_view(view_id: str, body: ViewPatch, auth: WriteAuth, session: DbSession) -> dict:
    view = views.update_view(session, auth.actor, view_id, body.model_dump(exclude_unset=True))
    return views.view_out(view)


@router.post("/views/{view_id}/archive", response_model=ViewOut)
def archive_view(view_id: str, auth: WriteAuth, session: DbSession) -> dict:
    return views.view_out(views.set_archived(session, auth.actor, view_id, archived=True))


@router.post("/views/{view_id}/unarchive", response_model=ViewOut)
def unarchive_view(view_id: str, auth: WriteAuth, session: DbSession) -> dict:
    return views.view_out(views.set_archived(session, auth.actor, view_id, archived=False))


def _parse_scope(scope: str | None) -> tuple[str | None, str | None]:
    if scope is None:
        return None, None
    if scope == "everything":
        return "everything", None
    kind, _, scope_id = scope.partition(":")
    if kind not in ("section", "list") or not scope_id:
        from slate.service.errors import Invalid

        raise Invalid("scope is 'everything', 'section:<id>' or 'list:<id>'")
    return kind, scope_id
