"""/batch: many MCP-style operations in one request (see `slate.batch`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from slate import batch
from slate.api.auth import DbSession, ReadAuth

router = APIRouter(tags=["batch"])


class OpResult(BaseModel):
    ok: bool
    result: Any = None
    error: str | None = None
    status: int | None = None


class BatchOut(BaseModel):
    atomic: bool
    results: list[OpResult]


@router.post("/batch", response_model=BatchOut, response_model_exclude_none=True)
def run_batch(body: batch.BatchIn, auth: ReadAuth, session: DbSession) -> dict:
    """Up to 100 operations in one round trip. Each op is an MCP call: `{tool, action, params,
    ref?}` (tool `item`, `comment`, `list`...; see docs/agents/reference/mcp.md); a
    string param `$ref.field` uses an earlier op's result. `atomic` (default) commits all or
    nothing: on a failure the answer is that op's error status with `failed_op` (its index) and
    nothing is written. `atomic: false` runs each op on its own and reports each slot. Each op
    needs its own scope (a read token can batch reads)."""
    return batch.run_batch(
        session, auth.principal, auth.actor, body.ops, atomic=body.atomic, views=body.views
    )
