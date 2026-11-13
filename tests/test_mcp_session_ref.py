"""The session ref an MCP call records on its events has the REST limit (200 characters), whether
it comes as the `session_ref` argument or as the `X-Slate-Session-Ref` header on `/mcp`."""

from __future__ import annotations

from sqlalchemy import func, select

from conftest import API
from slate.db import SessionLocal
from slate.models import Event
from test_mcp import call, err, http_mcp, ok


def _longest_ref() -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.max(func.length(Event.session_ref)))) or 0


async def test_an_overlong_session_ref_header_is_refused(alex, todo, client):
    long_ref = "R" * 8000
    r = client.post(
        f"{API}/items",
        headers={**alex, "X-Slate-Session-Ref": long_ref},
        json={"list_id": todo["id"], "title": "via REST"},
    )
    assert r.status_code == 422
    async with http_mcp({**alex, "X-Slate-Session-Ref": long_ref}) as mcp:
        msg = err(await call(mcp, "slate_item", "create", list_id=todo["id"], title="via MCP"))
    assert "200" in msg
    assert _longest_ref() <= 200


async def test_a_session_ref_header_within_the_limit_is_recorded(alex, todo):
    ref = "S" * 200
    async with http_mcp({**alex, "X-Slate-Session-Ref": ref}) as mcp:
        ok(await call(mcp, "slate_item", "create", list_id=todo["id"], title="via MCP"))
    with SessionLocal() as s:
        assert s.scalar(select(Event.session_ref).where(Event.type == "item.created")) == ref
