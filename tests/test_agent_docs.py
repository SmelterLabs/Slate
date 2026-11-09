"""The agent docs are checked against the code: generated reference current, every MCP action
maps to a real route with the same scope, every event type documented, and every route the
hand-written pages mention exists."""

from __future__ import annotations

from pathlib import Path

import pytest

from slate import docgen

DOCS = Path(__file__).resolve().parent.parent / "docs" / "agents"


def test_generated_reference_is_current():
    stale = [
        str(path.relative_to(docgen.ROOT))
        for path, text in docgen.render_all().items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]
    assert not stale, f"run `uv run slate docs` and commit: {stale}"


def test_every_event_type_in_the_code_is_documented():
    missing = docgen.event_types_in_code() - set(docgen.EVENT_DOCS)
    assert not missing, f"add a line to docgen.EVENT_DOCS for: {sorted(missing)}"


def test_documented_event_types_still_exist():
    gone = set(docgen.EVENT_DOCS) - docgen.event_types_in_code()
    assert not gone, f"no code writes these any more; drop them from EVENT_DOCS: {sorted(gone)}"


@pytest.mark.parametrize(
    ("tool", "action"), [(t, a) for t, a, _ in docgen.mcp_actions()], ids=lambda v: v
)
def test_mcp_action_calls_a_real_route_with_the_same_scope(tool, action):
    from slate.mcp_tools import TOOLS

    act = TOOLS[tool].actions[action]
    call = docgen.rest_call(act)
    assert call is not None, "its REST binding could not be built from sample params"
    op = docgen.match_route(*call)
    assert op is not None, f"{call[0]} {call[1]} is not a route"
    assert op.scope == act.scope, f"MCP needs {act.scope!r}, {op.method} {op.path} {op.scope!r}"


@pytest.mark.parametrize("page", sorted(p.name for p in DOCS.glob("*.md")))
def test_routes_mentioned_in_the_guides_exist(page):
    assert docgen.unknown_route_mentions((DOCS / page).read_text(encoding="utf-8")) == []


def test_route_mention_checker_catches_a_typo():
    assert docgen.unknown_route_mentions("call `GET /itemz/{id}` then `GET /items/<id>`") == [
        "GET /itemz/{id}"
    ]
