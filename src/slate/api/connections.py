"""Generic pairing and private connection health; local runtime authority stays local."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from slate.api.auth import AdminAuth, DbSession, ReadAuth, WriteAuth
from slate.api.collaboration import Attempts
from slate.service import connections
from slate.service.core import Actor

router = APIRouter(prefix="/agent-connections", tags=["agent connections"])
issue_attempts = Attempts()
exchange_attempts = Attempts()


class PairingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    label: str = Field(min_length=1, max_length=80)
    list_ids: list[str] = Field(min_length=1, max_length=100)
    ttl_seconds: int = Field(default=600, ge=60, le=900)


class ExchangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=100)
    device_name: str = Field(min_length=1, max_length=80)
    adapter: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(max_length=6)
    version: str = Field(min_length=1, max_length=40)


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: Literal["connected", "probe_passed", "ready", "runtime_unavailable", "needs_setup"]
    capabilities: list[str] | None = Field(default=None, max_length=6)


@router.post("/pairings", status_code=201)
def issue(body: PairingIn, auth: AdminAuth, session: DbSession, response: Response) -> dict:
    """Create explicit list-scoped consent; return the expiring single-use code once."""
    issue_attempts.take(auth.principal.token_id)
    # No user-supplied session reference on secret-bearing operations.
    actor = Actor(auth.account.id, auth.actor.source)
    connection, code = connections.issue(session, actor, by=auth.principal, **body.model_dump())
    response.headers["Cache-Control"] = "no-store"
    return {"connection": connection, "code": code, "expires_at": connection["expires_at"]}


@router.post("/exchange")
async def exchange(request: Request, session: DbSession, response: Response) -> dict:
    """Exchange one code for one scoped credential. Validation never echoes secret input."""
    exchange_attempts.take(request.client.host if request.client else "unknown")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 4096:
            raise HTTPException(422, "Invalid exchange request.")
    try:
        body = ExchangeIn.model_validate_json(data)
    except (ValidationError, ValueError):
        raise HTTPException(422, "Invalid exchange request.") from None
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return await run_in_threadpool(connections.exchange, session, **body.model_dump())


@router.get("")
def listing(account_id: str, auth: ReadAuth, session: DbSession, response: Response) -> dict:
    """Private connection lifecycle for self or unrestricted account managers only."""
    response.headers["Cache-Control"] = "no-store"
    return {
        "connections": connections.list_connections(
            session, by=auth.principal, account_id=account_id
        )
    }


@router.post("/{connection_id}/heartbeat")
def heartbeat(connection_id: str, body: HeartbeatIn, auth: WriteAuth, session: DbSession) -> dict:
    """Exact connection credential reports a canned health check, not arbitrary diagnostics."""
    return connections.heartbeat(
        session,
        Actor(auth.account.id, auth.actor.source),
        by=auth.principal,
        connection_id=connection_id,
        **body.model_dump(),
    )


@router.delete("/{connection_id}", status_code=204)
def revoke(connection_id: str, auth: ReadAuth, session: DbSession) -> Response:
    """Disconnect one credential without claiming a remote process has stopped."""
    connections.revoke(
        session,
        Actor(auth.account.id, auth.actor.source),
        by=auth.principal,
        connection_id=connection_id,
    )
    return Response(status_code=204)
