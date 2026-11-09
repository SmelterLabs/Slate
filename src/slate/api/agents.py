"""/agents: the roster of agents with presence and current work."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from slate.api.auth import DbSession, ReadAuth
from slate.api.schemas import AssigneeOut
from slate.ratelimit import limiter
from slate.service import agents

router = APIRouter(tags=["agents"])


class WorkOut(BaseModel):
    item_id: str
    title: str
    list_id: str
    state: Literal["working", "blocked", "review"]
    note: str | None
    progress: int | None
    url: str | None = None
    at: datetime


class AgentOut(BaseModel):
    account: AssigneeOut
    last_seen_at: datetime | None
    last_seen_via: str | None
    online: bool
    live_streams: int
    working: list[WorkOut]
    open_assigned: int
    events_24h: int
    last_event_at: datetime | None
    throttled: int
    wake: Literal["push", "pull", "manual"] = "push"


class PlanNextOut(BaseModel):
    item_id: str
    title: str
    list: str | None = Field(description="The list's name")
    due_at: datetime | None
    reason: Literal["due", "run_requested", "ask"] = Field(
        description="due: its run time comes (06:00 workspace time for a date-only due date, "
        "else the due time; null `at` = no due date, takeable now); run_requested: Run now was "
        "pressed; "
        "ask: an open Ask addressed to it"
    )
    at: datetime | None = Field(description="When it becomes takeable (null: now)")


class PlanLeaseOut(BaseModel):
    item_id: str
    title: str | None
    claimed_at: datetime
    until: datetime
    age_seconds: int = Field(description="How long it has held the task")


class AgentPlanOut(BaseModel):
    id: str
    handle: str
    display_name: str
    next: list[PlanNextOut] = Field(description="Soonest first")
    leases: list[PlanLeaseOut] = Field(description="Tasks it holds now, soonest to expire first")


class PlanOut(BaseModel):
    horizon_hours: int = Field(description="How far ahead `next` looks")
    agents: list[AgentPlanOut] = Field(description="Agents with the soonest work first")


@router.get("/agents/plan", response_model=PlanOut)
def agents_plan(_: ReadAuth, session: DbSession) -> dict:
    """What each agent is about to run in the next 24 hours, read with the work loop's own
    rule (`POST /work/next`): tasks whose run time comes (the due gate), tasks someone pressed
    Run now on, open Asks addressed to it, and the tasks it holds now with their age. Agents
    with the soonest work first. A restricted token sees only the agents and lists it reaches."""
    return {
        "horizon_hours": int(agents.PLAN_HORIZON.total_seconds() // 3600),
        "agents": agents.plan(session),
    }


@router.get("/agents", response_model=list[AgentOut])
def list_agents(
    _: ReadAuth, session: DbSession, kind: Literal["agent", "person", "all"] = "agent"
) -> list[dict]:
    """Who is around and what each is on: `online` (a request in the last 2 minutes or an open
    live stream), `last_seen_at`, `working` (items with that account's current report),
    `open_assigned`, `events_24h`, `throttled` (requests refused by the rate limit since
    the process started) and `wake` (how a task reaches it: push, pull or manual). Agents by
    default; `kind=person` or `all`."""
    return agents.roster(
        session, kind=None if kind == "all" else kind, throttled=dict(limiter.throttled)
    )
