"""Every MCP action accepts exactly what its REST route accepts.

A field added to a REST model and not to its MCP twin would be silently lost for agents.
This test fills an action's params with traceable dummy values, runs its `rest=`
binding to see what it sends (path, query, body), and compares that with the route's own
query parameters and body model from the OpenAPI schema. Three kinds of drift fail:

- REST accepts a field the action never sends (the MCP params lack it, or the binding drops it).
- The action sends a key the route does not accept (a 422 in stdio mode).
- The MCP params carry a field that reaches neither the path, the query nor the body.

Deliberate differences are listed in `ALLOW`, one line and one reason each.
"""

from __future__ import annotations

import json
import types
import typing
from datetime import date, datetime
from enum import Enum
from functools import cache
from typing import Any, Literal

import pytest
from pydantic import BaseModel

from slate import docgen
from slate.mcp_tools import TOOLS

# (tool, action) -> {field: reason}. A field named here is skipped in every direction.
# "*" covers the whole action (its REST body is deliberately a subset: a narrow action).
ALLOW: dict[tuple[str, str], dict[str, str]] = {
    ("slate_item", "set_status"): {"*": "narrow PATCH: only `status`"},
    ("slate_item", "assign"): {"*": "narrow PATCH: only `assignees`"},
    ("slate_item", "tag"): {"*": "narrow PATCH: only `tags`"},
    ("slate_item", "comment"): {"*": "narrow POST: only `body_md`; `slate_comment add` has all"},
    ("slate_comment", "assign"): {"*": "narrow PATCH: only `assignee`"},
    ("slate_comment", "edit"): {"*": "narrow PATCH: only `body_md`"},
    ("slate_share", "private"): {"*": "narrow PATCH on a section or list: only `private`"},
    ("slate_work", "finish"): {"next": "MCP-only: also takes the next task in the same call"},
}


def _dummy(annotation: Any, name: str, n: int) -> Any:
    """A value for `name` that can be traced in the REST call: strings carry the name."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        real = [a for a in args if a is not type(None)]
        return _dummy(real[0], name, n)
    if origin is typing.Annotated:
        return _dummy(args[0], name, n)
    if origin is Literal:
        return args[0]
    if origin in (list, set, tuple):
        return [_dummy(args[0], name, n)] if args else [f"X{name}X"]
    if origin is dict or annotation is dict:
        return {"k": f"X{name}X"}
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return filled(annotation)
        if issubclass(annotation, bool):
            return True
        if issubclass(annotation, int):
            return 1000 + n
        if issubclass(annotation, float):
            return 0.5
        if issubclass(annotation, Enum):
            return next(iter(annotation))
        if issubclass(annotation, datetime):
            return datetime(2030, 1, 2, 3, 4, 5)
        if issubclass(annotation, date):
            return date(2030, 1, 2)
    return f"X{name}X"


def filled(model: type[BaseModel]) -> BaseModel:
    """The model with every field set (validation skipped: the values are markers)."""
    values = {
        name: _dummy(field.annotation, name, i)
        for i, (name, field) in enumerate(model.model_fields.items())
    }
    return model.model_construct(_fields_set=set(values), **values)


@cache
def _openapi() -> dict[str, Any]:
    from slate.app import create_app

    return create_app().openapi()


def _ref(schema: dict[str, Any] | None) -> str | None:
    if not schema:
        return None
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for alt in schema.get("anyOf", []):
        if "$ref" in alt:
            return alt["$ref"].rsplit("/", 1)[-1]
    return None


def rest_accepts(method: str, path: str) -> tuple[set[str], set[str]]:
    """(query parameter names, body field names) of a route, from the OpenAPI schema."""
    spec = _openapi()
    op = spec["paths"][path][method.lower()]
    query = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
    body: set[str] = set()
    content = op.get("requestBody", {}).get("content", {})
    schema = content.get("application/json", {}).get("schema")
    name = _ref(schema)
    if name:
        body = set(spec["components"]["schemas"][name].get("properties", {}))
    return query, body


def _traced(name: str, value: Any, sent: set[str], blob: str, path: str) -> bool:
    if name in sent:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        marker = f"X{name}X"
        # A Literal value may pick the path (`scope_kind` -> /sections/ or /lists/).
        return marker in blob or marker in path or (value != marker and value in path)
    if isinstance(value, int | float):
        return f"/{value}" in path or f"/{value}/" in path or json.dumps(value) in blob
    return f"X{name}X" in blob


ACTIONS = [(t, a) for t, spec in TOOLS.items() for a in spec.actions]


@pytest.mark.parametrize(("tool", "action"), ACTIONS, ids=lambda v: v)
def test_mcp_params_match_the_rest_route(tool, action):
    act = TOOLS[tool].actions[action]
    params = filled(act.params)
    call = act.rest(params)
    full = docgen.API + call.path
    op = docgen.match_route(call.method, full)
    assert op is not None, f"{call.method} {full} is not a route"
    rest_query, rest_body = rest_accepts(op.method, op.path)
    sent_query = set(call.query or {})
    sent_body = set(call.body) if isinstance(call.body, dict) else set()
    allow = ALLOW.get((tool, action), {})
    narrow = "*" in allow

    problems: list[str] = []
    for name in sorted((rest_query | rest_body) - (sent_query | sent_body) - set(allow)):
        if not narrow:
            problems.append(f"REST accepts `{name}` but the MCP action never sends it")
    for name in sorted((sent_query | sent_body) - (rest_query | rest_body) - set(allow)):
        problems.append(f"the MCP action sends `{name}`, which {op.method} {op.path} rejects")
    blob = json.dumps(
        {"q": call.query, "b": call.body}, default=lambda v: getattr(v, "value", str(v))
    )
    for name in act.params.model_fields:
        if name in allow:
            continue
        if not _traced(name, getattr(params, name), sent_query | sent_body, blob, call.path):
            problems.append(f"MCP param `{name}` reaches neither the path, query nor body")
    assert not problems, f"{tool} {action} vs {op.method} {op.path}:\n  " + "\n  ".join(problems)


@cache
def _routes() -> dict[tuple[str, str], Any]:
    import pkgutil
    from importlib import import_module

    from fastapi.routing import APIRoute

    import slate.api

    out = {}
    for info in pkgutil.iter_modules(slate.api.__path__):
        router = getattr(import_module(f"slate.api.{info.name}"), "router", None)
        for route in getattr(router, "routes", []):
            if isinstance(route, APIRoute):
                path = route.path if route.path.startswith(docgen.API) else docgen.API + route.path
                out.update({(method, path): route for method in route.methods})
    return out


@pytest.mark.parametrize(("tool", "action"), ACTIONS, ids=lambda v: v)
def test_mcp_scope_is_the_rest_route_scope(tool, action):
    """The MCP door checks `Action.scope` itself (the REST `require` never runs there), so it
    must be the scope the route demands: a lower one lets a read token write over MCP."""
    from slate.mcp_tools import _scope_of

    act = TOOLS[tool].actions[action]
    call = act.rest(filled(act.params))
    op = docgen.match_route(call.method, docgen.API + call.path)
    assert op is not None, f"{call.method} {call.path} is not a route"
    assert act.scope == _scope_of(_routes()[(op.method, op.path)]), (tool, action)
