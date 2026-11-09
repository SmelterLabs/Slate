"""/workload/boxes and /capacities: the Box view's numbers and what each account can take on."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.service import workload

router = APIRouter(tags=["workload"])

Kinds = Literal["all", "people", "agents"]


class CapacityOut(BaseModel):
    account_id: str
    custom: bool = Field(description="False: the defaults for the account's kind apply")
    hours_per_day: float | None = Field(description="For time estimates; null = no limit")
    tasks_per_day: int | None = Field(description="For task counts; null = no limit")
    work_days: list[int] = Field(description="Weekdays worked, 0 = Monday")


class CapacityIn(BaseModel):
    """Send only what changes; null means no limit in that unit."""

    model_config = ConfigDict(extra="forbid")

    hours_per_day: float | None = Field(default=None, ge=0, le=24)
    tasks_per_day: int | None = Field(default=None, ge=0, le=1000)
    work_days: list[int] | None = Field(default=None, description="0 = Monday … 6 = Sunday")


class RowAccount(BaseModel):
    id: str
    handle: str
    display_name: str
    kind: Literal["person", "agent"]


class Box(BaseModel):
    account: RowAccount | None = Field(description="null: the box for tasks with no assignee")
    open: int = Field(description="Open tasks in a status of kind open")
    active: int = Field(description="Open tasks in a status of kind active (in progress)")
    overdue: int
    done: int = Field(description="Finished in the last done_days days")
    minutes: int = Field(description="Time estimates of the open tasks")
    capacity_minutes: float | None = Field(description="This week's capacity; null = no limit")
    over: bool = Field(description="Open minutes above this week's capacity")


class BoxesOut(BaseModel):
    today: str
    done_days: int
    week_start: str
    boxes: list[Box]


@router.get("/workload/boxes", response_model=BoxesOut)
def get_boxes(
    auth: ReadAuth,
    session: DbSession,
    kinds: Kinds = "all",
    section_id: str | None = None,
    list_id: str | None = None,
    done_days: int = 7,
) -> dict:
    """The Box view's numbers: per person and agent, plus one box for unassigned tasks, open
    and in-progress counts, overdue, finished in the last `done_days` days, and open load
    against this week's capacity."""
    return workload.boxes(
        session, kinds=kinds, section_id=section_id, list_id=list_id, done_days=done_days
    )


@router.get("/capacities", response_model=list[CapacityOut])
def list_capacities(auth: ReadAuth, session: DbSession, kinds: Kinds = "all") -> list[dict]:
    """Every person's and agent's capacity, defaults included."""
    return workload.list_capacities(session, kinds)


@router.get("/accounts/{account_id}/capacity", response_model=CapacityOut)
def get_capacity(account_id: str, auth: ReadAuth, session: DbSession) -> dict:
    return workload.get_capacity(session, account_id)


@router.patch("/accounts/{account_id}/capacity", response_model=CapacityOut)
def set_capacity(account_id: str, body: CapacityIn, auth: WriteAuth, session: DbSession) -> dict:
    """Set an account's capacity: your own, or anyone's as an owner or admin."""
    return workload.set_capacity(
        session, auth.actor, account_id, body.model_dump(exclude_unset=True)
    )


@router.delete("/accounts/{account_id}/capacity", response_model=CapacityOut)
def reset_capacity(account_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Back to the defaults for the account's kind."""
    return workload.reset_capacity(session, auth.actor, account_id)
