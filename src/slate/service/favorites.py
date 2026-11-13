"""Favorites: one account's pinned lists, sections, saved views, items and docs (the favorites bar).

Favorites belong to the caller: every function takes the actor's account as the owner, so an
agent pins for itself and a person's bar is their own. Adding is idempotent (the existing favorite
comes back unchanged); removing deletes the row and records `favorite.removed`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Doc, Favorite, FavoriteKind, Item, List, Section, View
from slate.service.core import Actor, apply_change
from slate.service.errors import Invalid, NotFound

MAX_FAVORITES = 50

_TARGETS: dict[FavoriteKind, type] = {
    FavoriteKind.list: List,
    FavoriteKind.section: Section,
    FavoriteKind.view: View,
    FavoriteKind.item: Item,
    FavoriteKind.doc: Doc,
}


def _kind(kind: str) -> FavoriteKind:
    try:
        return FavoriteKind(kind)
    except ValueError:
        raise Invalid("kind must be 'list', 'section', 'view', 'item' or 'doc'") from None


def list_favorites(session: Session, account_id: str) -> list[Favorite]:
    stmt = (
        select(Favorite)
        .where(Favorite.account_id == account_id)
        .order_by(Favorite.position, Favorite.created_at)
    )
    return list(session.scalars(stmt))


def get_favorite(session: Session, account_id: str, favorite_id: str) -> Favorite:
    fav = session.get(Favorite, favorite_id)
    if fav is None or fav.account_id != account_id:
        raise NotFound(f"favorite {favorite_id} not found")
    return fav


def favorite_out(session: Session, fav: Favorite) -> dict[str, Any]:
    """The favorite plus what a client needs to draw and open its chip without another call."""
    target = session.get(_TARGETS[fav.kind], fav.target_id)
    out: dict[str, Any] = {
        "id": fav.id,
        "kind": fav.kind.value,
        "target_id": fav.target_id,
        "position": fav.position,
        "created_at": fav.created_at,
        "name": None,
        "scope_kind": None,
        "scope_id": None,
        "available": False,
    }
    if target is None:
        return out
    out["available"] = target.archived_at is None
    if fav.kind is FavoriteKind.item:
        out["name"] = target.title
        out["scope_kind"], out["scope_id"] = "list", target.list_id
    elif fav.kind is FavoriteKind.doc:
        out["name"] = f"{target.icon} {target.title}" if target.icon else target.title
        out["scope_kind"], out["scope_id"] = "doc", target.id
    elif fav.kind is FavoriteKind.view:
        out["name"] = target.name
        out["scope_kind"], out["scope_id"] = target.scope_kind.value, target.scope_id
    else:
        out["name"] = target.name
        out["scope_kind"], out["scope_id"] = fav.kind.value, target.id
    return out


def add_favorite(
    session: Session, actor: Actor, *, kind: str, target_id: str, position: int | None = None
) -> Favorite:
    """Pin a target for the actor. Pinning it again returns the existing favorite."""
    k = _kind(kind)
    if session.get(_TARGETS[k], target_id) is None:
        raise NotFound(f"{k.value} {target_id} not found")
    mine = list_favorites(session, actor.account_id)
    for fav in mine:
        if fav.kind is k and fav.target_id == target_id:
            return fav
    if len(mine) >= MAX_FAVORITES:
        raise Invalid(f"at most {MAX_FAVORITES} favorites; remove one first")
    with apply_change(session, actor) as cx:
        if position is None or position >= len(mine):
            at = mine[-1].position + 1 if mine else 0
        else:
            # Land at `position` in the bar's order: renumber everything from there on.
            position = max(position, 0)
            for i, fav in enumerate(mine[position:], start=position + 1):
                cx.set(fav, "position", i, "favorite.moved")
            at = position
        fav = cx.create(
            Favorite(account_id=actor.account_id, kind=k, target_id=target_id, position=at),
            "favorite.added",
        )
    return fav


def remove_favorite(session: Session, actor: Actor, favorite_id: str) -> None:
    fav = get_favorite(session, actor.account_id, favorite_id)
    with apply_change(session, actor) as cx:
        cx.delete(fav, "favorite.removed")


def reorder_favorites(session: Session, actor: Actor, ids: list[str]) -> list[Favorite]:
    """Put the actor's favorites in this order. `ids` must name every favorite exactly once."""
    mine = list_favorites(session, actor.account_id)
    by_id = {f.id: f for f in mine}
    if len(ids) != len(set(ids)) or set(ids) != set(by_id):
        raise Invalid("ids must list each of your favorites exactly once")
    with apply_change(session, actor) as cx:
        for i, fid in enumerate(ids):
            cx.set(by_id[fid], "position", i, "favorite.moved")
    return list_favorites(session, actor.account_id)
