"""Batch: many operations in one request, all-or-nothing by default.

    POST /api/v1/batch
    {"ops": [
        {"ref": "t", "tool": "item", "action": "create",
         "params": {"list_id": "Ab12Cd34Ef56", "title": "Renew passport"}},
        {"tool": "comment", "action": "add", "params": {"id": "$t.id", "body_md": "Filed"}}
    ]}

An op is exactly an MCP call (`tool`, `action`, `params`): the same action table
(`slate.mcp_tools.TOOLS`), the same params models, the same service functions, the same output.
`tool` may drop its `slate_` prefix. A string param that is exactly `$<ref>` or
`$<ref>.<path>` is replaced by an earlier op's result (`$t.id`, `$t.status.name`).

`atomic` (default true) runs every op inside one `apply_change()` block: one transaction, one
commit, events published only if every op succeeds, and the first failure rolls everything
back. With `atomic: false` each op commits on its own and a failure is reported in its slot
while the rest carry on. Either way a batch is one round trip: 50 creates cost one request
instead of fifty.

`views` says which state of a task an op answers with. `"final"` (default): every write op
that answers with a task (create, update, set_status, report, ...) answers with the task as
the whole batch left it, built once after the commit for all ops together, outside the write
lock. Two ops on one task therefore both show the end state, and a task deleted for good later
in the batch answers `null`. `"each"`: each op answers with the task as it was right after that
op, built inside the batch (slower, and an atomic batch holds the write lock while it
builds). Read ops (`get`, `query`, ...) and `$ref` lookups always see the state at
their point in the batch: `$t.status.name` is the status right after op `t`.

Filing several items in one batch is several separate `POST /items`; it never creates children
(AGENTS.md rule 1). Webhook tests (network I/O) cannot run inside a batch, nor can a batch,
nor can a long-poll: an op with `wait` above 0 (`checkin`, `work next`, `work finish` with
`next.wait`) is refused before it runs, atomic or not. A wait inside an atomic batch would
hold the write lock while it slept.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from slate.models import Item
from slate.service.accounts import Principal
from slate.service.core import Actor, apply_change
from slate.service.errors import Forbidden, Invalid, ServiceError

MAX_OPS = 100
# Actions that talk to another server (the webhook test) must not hold the write lock an
# atomic batch takes, and nested batches are refused.
EXCLUDED = {
    ("slate_webhook", "test"),
    ("slate_batch", "run"),
}
_REF = re.compile(r"^\$([a-z][a-z0-9_]{0,31})((?:\.[A-Za-z0-9_]+)*)$")


class Op(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(description="MCP tool name, with or without `slate_` (item, comment, ...)")
    action: str | None = Field(
        default=None, description="The tool's action; omit for single-action tools"
    )
    params: dict[str, Any] = Field(default_factory=dict)
    ref: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,31}$",
        description="Name this op's result so later ops can say `$name.field`",
    )


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[Op] = Field(min_length=1, max_length=MAX_OPS)
    atomic: bool = Field(
        default=True, description="All or nothing (default); false = each op commits alone"
    )
    views: Literal["final", "each"] = Field(
        default="final",
        description="Tasks in write results: `final` (default, faster) = as the whole batch "
        "left them; `each` = as each op left them",
    )


class Later:
    """A task a write op answers with, built after the batch commits (`views="final"`)."""

    __slots__ = ("id",)

    def __init__(self, item_id: str) -> None:
        self.id = item_id


class BatchFailed(ServiceError):
    """An atomic batch stopped at op `index`; nothing was written."""

    def __init__(self, index: int, cause: Exception) -> None:
        super().__init__(f"op {index}: {cause}. Nothing was written.")
        self.index = index
        self.cause = cause


def _resolve(value: Any, refs: dict[str, Any], session: Any) -> Any:
    if isinstance(value, str):
        m = _REF.match(value)
        if not m:
            return value
        name, path = m.group(1), m.group(2)
        if name not in refs:
            raise Invalid(f"`{value}` names no earlier op (refs so far: {sorted(refs) or 'none'})")
        out = refs[name]
        for key in filter(None, path.split(".")):
            if isinstance(out, Later):
                if key == "id":  # the common `$t.id` needs no view
                    out = out.id
                    continue
                out = _now(session, out)
            if isinstance(out, dict) and key in out:
                out = out[key]
            elif isinstance(out, list) and key.isdigit() and int(key) < len(out):
                out = out[int(key)]
            else:
                raise Invalid(f"`{value}`: the result of `{name}` has no `{key}`")
        return _now(session, out)
    if isinstance(value, dict):
        return {k: _resolve(v, refs, session) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, refs, session) for v in value]
    return value


def _now(session: Any, value: Any) -> Any:
    """`value` with its `Later` marks built as the task stands now (inside the batch)."""
    if isinstance(value, Later):
        from slate.mcp_tools import _item_views

        row = session.get(Item, value.id)
        return _item_views(_Views(session), [row])[0] if row is not None else None
    if isinstance(value, list) and any(isinstance(v, Later) for v in value):
        return [_now(session, v) for v in value]
    return value


class _Views:
    """Enough of `mcp_tools.Local` to build item views."""

    later = None

    def __init__(self, session: Any) -> None:
        self.session = session


def _marks(value: Any, into: list[str]) -> None:
    if isinstance(value, Later):
        into.append(value.id)
    elif isinstance(value, list):
        for v in value:
            _marks(v, into)
    elif isinstance(value, dict):
        for v in value.values():
            _marks(v, into)


def _fill(value: Any, built: dict[str, dict]) -> Any:
    if isinstance(value, Later):
        return built.get(value.id)
    if isinstance(value, list):
        return [_fill(v, built) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, built) for k, v in value.items()}
    return value


def _finish(session: Any, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build every task the results name, once each and all together, after the commit."""
    from slate.mcp_tools import _item_views

    ids: list[str] = []
    _marks(results, ids)
    if not ids:
        return results
    wanted = list(dict.fromkeys(ids))
    rows = list(session.scalars(select(Item).where(Item.id.in_(wanted))))
    built = {v["id"]: v for v in _item_views(_Views(session), rows)}
    return _fill(results, built)


def _prepare(
    session: Any, op: Op, refs: dict[str, Any], principal: Principal
) -> tuple[Any, BaseModel]:
    from slate.mcp_tools import TOOLS, ToolError, single_action

    name = op.tool if op.tool.startswith("slate_") else f"slate_{op.tool}"
    spec = TOOLS.get(name)
    if spec is None:
        raise Invalid(f"unknown tool '{op.tool}'")
    action_name = op.action or (next(iter(spec.actions)) if single_action(spec) else None)
    if action_name is None:
        raise Invalid(f"{name} needs an action: one of {', '.join(spec.actions)}")
    action = spec.actions.get(action_name)
    if action is None:
        raise Invalid(f"unknown action '{action_name}' for {name}")
    if (name, action_name) in EXCLUDED or action.local is None:
        raise Invalid(f"{name} {action_name} cannot run inside a batch")
    if not principal.has(action.scope):
        raise Forbidden(f"this token lacks the '{action.scope}' scope for {name} {action_name}")
    try:
        params = action.params.model_validate(_resolve(op.params, refs, session))
    except ValidationError as exc:
        msg = "; ".join(
            f"{'.'.join(str(x) for x in e['loc']) or 'params'}: {e['msg']}" for e in exc.errors()
        )
        raise Invalid(msg) from None
    except ToolError as exc:  # pragma: no cover - params models do not raise it today
        raise Invalid(str(exc)) from None
    if _waits(params):
        raise Invalid(
            f"{name} {action_name}: wait cannot be used inside a batch (a batch never "
            "long-polls); call it on its own, or pass wait 0"
        )
    return action, params


def _waits(params: BaseModel) -> bool:
    """Does this op long-poll (`wait`, or `next.wait` on `work finish`)?"""
    if (getattr(params, "wait", 0) or 0) > 0:
        return True
    nxt = getattr(params, "next", None)
    if isinstance(nxt, BaseModel):
        return (getattr(nxt, "wait", 0) or 0) > 0
    if isinstance(nxt, dict):
        try:
            return float(nxt.get("wait") or 0) > 0
        except (TypeError, ValueError):
            return False  # the action refuses the bad value itself
    return False


def _run_one(
    session: Any, principal: Principal, actor: Actor, op: Op, refs: dict, final: bool
) -> Any:
    from slate.mcp_tools import Local, ToolError

    action, params = _prepare(session, op, refs, principal)
    # Only writes defer their views: a read answers with what it saw at its point.
    later = [] if final and action.scope != "read" else None
    try:
        result = action.local(Local(session, principal, actor, later), params)
    except ToolError as exc:
        raise Invalid(str(exc)) from None
    if op.ref:
        refs[op.ref] = result
    return result


def run_batch(
    session: Any,
    principal: Principal,
    actor: Actor,
    ops: list[Op],
    *,
    atomic: bool = True,
    views: str = "final",
) -> dict[str, Any]:
    """Run `ops` in order. Returns `{atomic, results: [{ok, result} | {ok, error}]}`.

    Atomic: raises `BatchFailed` (carrying the op index) on the first failure, writing nothing.
    `views="final"` builds the tasks write ops answer with after the commit (see the module).
    """
    final = views == "final"
    refs: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    if atomic:
        with apply_change(session, actor):
            for i, op in enumerate(ops):
                try:
                    result = _run_one(session, principal, actor, op, refs, final)
                except ServiceError as exc:
                    raise BatchFailed(i, exc) from exc
                results.append({"ok": True, "result": result})
        return {"atomic": True, "results": _finish(session, results)}

    for op in ops:
        try:
            result = _run_one(session, principal, actor, op, refs, final)
            results.append({"ok": True, "result": result})
        except ServiceError as exc:
            session.rollback()
            results.append({"ok": False, "error": str(exc), "status": _status_of(exc)})
    return {"atomic": False, "results": _finish(session, results)}


def _status_of(exc: ServiceError) -> int:
    from slate.api import status_of

    return status_of(exc)
