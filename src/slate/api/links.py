"""/items/{id}/links (dependencies)."""

from __future__ import annotations

from fastapi import APIRouter

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import LinkIn, LinkKind, LinksOut
from slate.service import items, links

router = APIRouter(tags=["links"])


# --- Dependencies --------------------------------------------------------------------------


def _links_out(session, item_id: str) -> dict:
    view = items.views(session, [items.get_item(session, item_id)])[0]
    return {k: view[k] for k in links.LINK_KEYS}


@router.get("/items/{item_id}/links", response_model=LinksOut)
def get_links(item_id: str, _: ReadAuth, session: DbSession) -> dict:
    return _links_out(session, item_id)


@router.post("/items/{item_id}/links", response_model=LinksOut)
def add_link(item_id: str, body: LinkIn, auth: WriteAuth, session: DbSession) -> dict:
    """Add a link from this item's side: it `blocks` / is `blocked_by` / `relates` to /
    is a `duplicate_of` / is `duplicated_by` `item_id`. Idempotent; a loop of blocks is
    refused."""
    links.add_link(session, auth.actor, item_id, kind=body.kind, other_id=body.item_id)
    return _links_out(session, item_id)


@router.delete("/items/{item_id}/links", response_model=LinksOut)
def remove_link(
    item_id: str, kind: LinkKind, other: str, auth: WriteAuth, session: DbSession
) -> dict:
    """Remove a link (`?kind=blocked_by&other=<item id>`). Missing is a no-op."""
    links.remove_link(session, auth.actor, item_id, kind=kind, other_id=other)
    return _links_out(session, item_id)
