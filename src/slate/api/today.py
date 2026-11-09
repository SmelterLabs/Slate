"""/today: the caller's overdue, due-today and waiting-on-me items."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response
from pydantic import TypeAdapter
from sqlalchemy import select

from slate.api.auth import DbSession, ReadAuth
from slate.api.items import JSON, parse_fields
from slate.api.schemas import ItemOut, TodayOut
from slate.models import Item
from slate.service import item_cache, items, today

router = APIRouter(tags=["today"])
GROUPS = ("overdue", "today", "waiting", "asked")
_ITEM = TypeAdapter(ItemOut)
_ITEMS = TypeAdapter(list[ItemOut])
_TIME = TypeAdapter(datetime)


@router.get("/today", response_model=TodayOut)
def get_today(
    auth: ReadAuth,
    session: DbSession,
    fields: Annotated[
        str | None,
        Query(description="Comma-separated item fields to return (id always); default all"),
    ] = None,
) -> Response:
    """Open items across all sections: overdue, due by midnight workspace time, waiting on
    the caller, and `asked` (an open Ask addressed to the caller, oldest first). The first
    three are sorted by priority, then due. `fields=id` is the cheap way to count them (the
    board's sidebar does that)."""
    wanted = parse_fields(fields)
    token = item_cache.cache.begin(session)
    view = today.today(session, auth.account.id, keys_only=True)
    ids = [[row.id for row in view[g]] for g in GROUPS]
    if wanted is None:  # whole items, from the item cache
        groups = [
            b"[" + b",".join(item_cache.render(session, g, items.views, _ITEM, token)) + b"]"
            for g in ids
        ]
    elif wanted == {"id"}:
        groups = [json.dumps([{"id": i} for i in g]).encode() for g in ids]
    else:
        groups = []
        for g in ids:
            found = {i.id: i for i in session.scalars(select(Item).where(Item.id.in_(g)))}
            rows = _ITEMS.validate_python(items.views(session, [found[i] for i in g]))
            groups.append(_ITEMS.dump_json(rows, include={"__all__": wanted}))
    body = b'{"now":%s,"day_ends_at":%s,"overdue":%s,"today":%s,"waiting":%s,"asked":%s}' % (
        _TIME.dump_json(view["now"]),
        _TIME.dump_json(view["day_ends_at"]),
        *groups,
    )
    return Response(body, media_type=JSON)
