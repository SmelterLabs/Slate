"""/health: is Slate working, and what broke (owners and admins). `GET /health` outside the API
is the same verdict without details, for anyone (slate.app)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from slate.api.auth import DbSession, ReadAuth
from slate.service import health

router = APIRouter(tags=["health"])


class HealthCheck(BaseModel):
    key: str
    label: str
    status: str  # ok | warn | fail | off
    detail: str | None = None


class HealthOut(BaseModel):
    status: str  # ok | warn | fail: the worst check
    at: datetime
    version: str
    uptime_s: float
    window_minutes: int
    slow_ms: float
    alert_after: int
    checks: list[HealthCheck]
    scheduler: dict[str, Any]
    pollers: list[dict[str, Any]]
    engines: list[HealthCheck]
    agents: dict[str, Any]
    latency: dict[str, Any]
    automations: dict[str, Any]
    queues: dict[str, Any]
    db: dict[str, Any]


@router.get("/health", response_model=HealthOut)
def get_health(auth: ReadAuth, session: DbSession) -> dict:
    """Slate's health, for owners and admins: `status` (ok, warn or fail: the worst of
    `checks`), each background poller (reminders, digest, due nudges, recurring,
    trash) with its last run, last success, error and failures in a row, the engines
    (scheduler, webhooks, automations, push), calls per token over the last hour
    (errors, error rate, slow calls, average and worst time) with recent examples, request
    latency percentiles, failed automation runs in the last day, queue depths and engine lag,
    and database size and free disk. Counters start over when Slate restarts. A poller that
    fails `alert_after` runs in a row records `health.alert` (and `health.recovered` once it
    works again)."""
    health.require_viewer(session, auth.account)
    return health.report(session)
