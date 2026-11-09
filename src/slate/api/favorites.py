"""/favorites: the caller's pinned lists, sections, saved views and items (the favorites bar)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.service import favorites

router = APIRouter(tags=["favorites"])

FavoriteKind = Literal["list", "section", "view", "item", "doc"]


class FavoriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FavoriteKind
    target_id: str = Field(min_length=1, max_length=12)
    position: int | None = Field(default=None, ge=0, description="Slot in the bar; omit for last")


class FavoriteOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(description="Every favorite id, in the new order")


class FavoriteOut(BaseModel):
    id: str
    kind: FavoriteKind
    target_id: str
    position: int
    created_at: datetime
    name: str | None = Field(description="The target's current name or title")
    scope_kind: Literal["everything", "section", "list", "doc"] | None = Field(
        description="Where the chip opens: the target's own scope, a view's scope, an item's "
        "list, or a doc page"
    )
    scope_id: str | None
    available: bool = Field(description="False when the target is archived or gone")


@router.get("/favorites", response_model=list[FavoriteOut])
def list_favorites(auth: ReadAuth, session: DbSession) -> list[dict]:
    """Your favorites in bar order."""
    found = favorites.list_favorites(session, auth.account.id)
    return [favorites.favorite_out(session, f) for f in found]


@router.post("/favorites", status_code=status.HTTP_201_CREATED, response_model=FavoriteOut)
def add_favorite(body: FavoriteIn, auth: WriteAuth, session: DbSession) -> dict:
    """Pin a list, section, saved view or item. Pinning it again returns the same favorite."""
    fav = favorites.add_favorite(session, auth.actor, **body.model_dump())
    return favorites.favorite_out(session, fav)


@router.put("/favorites/order", response_model=list[FavoriteOut])
def reorder_favorites(body: FavoriteOrder, auth: WriteAuth, session: DbSession) -> list[dict]:
    found = favorites.reorder_favorites(session, auth.actor, body.ids)
    return [favorites.favorite_out(session, f) for f in found]


@router.delete("/favorites/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(favorite_id: str, auth: WriteAuth, session: DbSession) -> Response:
    favorites.remove_favorite(session, auth.actor, favorite_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
