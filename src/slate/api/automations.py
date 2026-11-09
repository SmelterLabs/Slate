"""/automations: when-this-then-that rules, their vocabulary and their run log."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.service import automations

router = APIRouter(tags=["automations"])


class AutomationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    list_id: str | None = Field(default=None, description="Scope: one list")
    section_id: str | None = Field(default=None, description="Scope: one section (or neither)")
    trigger: dict[str, Any] = Field(
        description="{type, ...params}, e.g. {type: status_changed, to: Done}; see /catalog"
    )
    conditions: list[dict[str, Any]] = Field(
        default_factory=list, description="[{field, op: is|is_not, value}], all must hold"
    )
    actions: list[dict[str, Any]] = Field(
        description="[{type, ...params}], e.g. [{type: add_tag, tag: shipped}]"
    )
    enabled: bool = True


class AutomationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    list_id: str | None = None
    section_id: str | None = None
    trigger: dict[str, Any] | None = None
    conditions: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None
    enabled: bool | None = None


class AutomationOut(BaseModel):
    id: str
    name: str
    list_id: str | None
    section_id: str | None
    trigger: dict[str, Any]
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    enabled: bool
    signed: bool = Field(description="Has a secret that signs its webhook actions")
    webhook_secret: str | None = Field(
        default=None,
        description="The webhook signing secret: only in the response that made it, never again",
    )
    description: str = Field(description="The rule in one plain-English line")
    created_by: str
    created_at: datetime
    updated_at: datetime
    run_count: int = Field(description="Runs in the last 30 days")
    last_run_at: datetime | None
    last_status: Literal["ok", "partial", "failed"] | None


class RunResult(BaseModel):
    action: str
    ok: bool
    detail: str


class RunOut(BaseModel):
    id: str
    automation_id: str
    automation_name: str | None
    event_id: str | None
    item_id: str | None
    item_title: str | None
    triggered_by: str | None = Field(description="Account whose change fired it, or who ran it")
    status: Literal["ok", "partial", "failed"]
    results: list[RunResult]
    at: datetime


class RunNowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(description="Run the rule's actions on this item now")


class CatalogParam(BaseModel):
    name: str
    kind: str
    required: bool
    help: str


class CatalogTrigger(BaseModel):
    type: str
    label: str
    event: str
    params: list[CatalogParam]


class CatalogAction(BaseModel):
    type: str
    label: str
    params: list[CatalogParam]


class CatalogConditions(BaseModel):
    fields: list[dict[str, str]]
    ops: list[str]


class CatalogOut(BaseModel):
    triggers: list[CatalogTrigger]
    conditions: CatalogConditions
    actions: list[CatalogAction]


@router.get("/automations/catalog", response_model=CatalogOut)
def catalog(auth: ReadAuth) -> dict:
    """Every trigger, condition and action a rule can use, with parameters."""
    return automations.catalog()


@router.get("/automations", response_model=list[AutomationOut])
def list_automations(
    auth: ReadAuth,
    session: DbSession,
    list_id: str | None = None,
    section_id: str | None = None,
) -> list[dict]:
    rules = automations.list_automations(session, list_id=list_id, section_id=section_id)
    return automations.automation_views(session, rules)


@router.post("/automations", status_code=status.HTTP_201_CREATED, response_model=AutomationOut)
def create_automation(body: AutomationIn, auth: WriteAuth, session: DbSession) -> dict:
    rule = automations.create_automation(session, auth.actor, **body.model_dump())
    return automations.automation_view(session, rule)


@router.get("/automations/{automation_id}", response_model=AutomationOut)
def get_automation(automation_id: str, auth: ReadAuth, session: DbSession) -> dict:
    return automations.automation_view(session, automations.get_automation(session, automation_id))


@router.patch("/automations/{automation_id}", response_model=AutomationOut)
def update_automation(
    automation_id: str, body: AutomationPatch, auth: WriteAuth, session: DbSession
) -> dict:
    rule = automations.update_automation(
        session, auth.actor, automation_id, body.model_dump(exclude_unset=True)
    )
    return automations.automation_view(session, rule)


@router.delete("/automations/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_automation(automation_id: str, auth: WriteAuth, session: DbSession) -> Response:
    automations.delete_automation(session, auth.actor, automation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/automations/{automation_id}/run", response_model=RunOut)
def run_automation(automation_id: str, body: RunNowIn, auth: WriteAuth, session: DbSession) -> dict:
    """Run the rule's actions on one item now (trigger and conditions skipped)."""
    from slate import automator

    run = automations.run_now(
        session, auth.actor, automation_id, item_id=body.item_id, send=automator.send
    )
    return automations.run_views(session, [run])[0]


class SecretOut(BaseModel):
    secret: str = Field(description="Verify X-Slate-Signature with it; shown only here")


@router.post("/automations/{automation_id}/secret", response_model=SecretOut)
def rotate_secret(automation_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Make a new webhook signing secret for the rule (the old one stops working)."""
    return {"secret": automations.rotate_secret(session, auth.actor, automation_id)}


@router.get("/automations/{automation_id}/runs", response_model=list[RunOut])
def automation_runs(
    automation_id: str,
    auth: ReadAuth,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    return automations.list_runs(session, automation_id=automation_id, limit=limit)


@router.get("/automation-runs", response_model=list[RunOut])
def all_runs(
    auth: ReadAuth, session: DbSession, limit: int = Query(default=50, ge=1, le=500)
) -> list[dict]:
    """The run log across every rule, newest first."""
    return automations.list_runs(session, limit=limit)
