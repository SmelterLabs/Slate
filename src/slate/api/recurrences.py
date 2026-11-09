"""/recurrences: recurring rules. Mounted by `install(app)` from `slate.app`.

Request and response models live here, not in `api/schemas.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, FastAPI, Query, status
from pydantic import BaseModel, ConfigDict, Field

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import ItemOut, Priority
from slate.service import items, recurrences
from slate.service.errors import Invalid

router = APIRouter(prefix="/api/v1", tags=["recurrences"])

MissedPolicy = Literal["spawn_always", "roll_forward", "skip_if_open"]
Trigger = Literal["schedule", "on_done"]


class _RuleFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_template_md: str | None = Field(
        default=None, description="Markdown; {date} and {iso_date} are filled in"
    )
    priority: Priority | None = None
    tags: list[str] | None = Field(default=None, description="Tag names for each instance")
    assignees: list[str] | None = Field(default=None, description="Account handles or ids")
    dtstart: str | None = Field(
        default=None,
        description="First occurrence anchor: YYYY-MM-DD (midnight in timezone) or ISO 8601",
    )
    timezone: str | None = Field(
        default=None,
        description="IANA zone; defaults to the workspace timezone (SLATE_TIMEZONE)",
    )
    lead_days: int | None = Field(default=None, ge=0, le=recurrences.MAX_LEAD_DAYS)
    missed_policy: MissedPolicy | None = None
    trigger: Trigger | None = Field(
        default=None,
        description="schedule (default): one item per date. on_done: the next one is made when "
        "the current one is marked done, dated from that day",
    )
    skip_weekends: bool | None = Field(
        default=None, description="Move a Saturday or Sunday date to the Monday after"
    )
    reopen: bool | None = Field(
        default=None,
        description="on_done only: reopen the same task with the next dates instead of "
        "making a new one",
    )
    copy_checklists: bool | None = Field(
        default=None,
        description="Each new one gets the previous one's checklists, unticked (default true)",
    )


class RecurrenceIn(_RuleFields):
    list_id: str
    title_template: str = Field(
        min_length=1,
        max_length=500,
        description='e.g. "Bulk trash {date}" -> "Bulk trash Wed Nov 11"',
    )
    rrule: str = Field(description="RFC 5545 RRULE, e.g. FREQ=WEEKLY;INTERVAL=2;BYDAY=WE")
    adopt_item_id: str | None = Field(
        default=None,
        description="Make this existing item the current occurrence (it keeps its due date; "
        "the rule spawns from the next one). dtstart then defaults to the item's due day.",
    )


class RecurrencePatch(_RuleFields):
    """Send only what changes. `ended: true` ends the rule for good."""

    list_id: str | None = None
    title_template: str | None = Field(default=None, min_length=1, max_length=500)
    rrule: str | None = None
    ended: bool | None = None


class OccurrenceOut(BaseModel):
    local: str = Field(description="Occurrence in the rule's timezone, ISO 8601 with offset")
    utc: str
    due_at: str = Field(description="When the spawned item is due, UTC")


class RecurrenceOut(BaseModel):
    id: str
    list_id: str
    title_template: str
    body_template_md: str | None
    priority: Priority | None
    tags: list[str]
    assignee_ids: list[str]
    rrule: str
    description: str = Field(
        description='The rule in plain English, e.g. "every 2 weeks on Wednesday"'
    )
    dtstart: datetime
    timezone: str
    lead_days: int
    missed_policy: MissedPolicy
    next_run_at: datetime | None = Field(description="The next occurrence not yet materialized")
    created_by: str
    paused_at: datetime | None
    ended_at: datetime | None
    trigger: Trigger
    skip_weekends: bool
    reopen: bool
    copy_checklists: bool
    next_occurrences: list[OccurrenceOut] = Field(
        description="The next dates (empty for trigger on_done: they depend on when it is done)"
    )


class PreviewOut(BaseModel):
    rrule: str = Field(description="The rule as it would be stored")
    description: str = Field(description='Plain English, e.g. "every month on the 2nd Wednesday"')
    timezone: str
    dtstart: datetime
    occurrences: list[OccurrenceOut] = Field(description="The next ones whose due is ahead")


class UpcomingOut(BaseModel):
    recurrence_id: str
    list_id: str
    title: str = Field(description="The title the item will get")
    occurrence_at: str
    due_at: str = Field(description="When it will be due, UTC")
    local_date: str = Field(description="Its day in the rule's timezone, YYYY-MM-DD")
    priority: Priority | None
    tags: list[str]
    assignee_ids: list[str]


class AdoptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(description="The item that becomes the rule's current occurrence")


class RunNowOut(BaseModel):
    recurrence: RecurrenceOut
    spawned: list[ItemOut]


@router.get("/recurrences", response_model=list[RecurrenceOut])
def list_recurrences(
    _: ReadAuth, session: DbSession, list_id: str | None = None, include_ended: bool = False
) -> list[dict]:
    rules = recurrences.list_recurrences(session, list_id=list_id, include_ended=include_ended)
    return [recurrences.recurrence_view(session, r) for r in rules]


@router.get("/recurrences/preview", response_model=PreviewOut)
def preview(
    _: ReadAuth,
    rrule: str,
    dtstart: str | None = None,
    timezone: str | None = None,
    count: int = Query(default=recurrences.NEXT_SHOWN, ge=1, le=50),
    trigger: Trigger | None = None,
    skip_weekends: bool = False,
) -> dict:
    """Try a rule without saving it: the next `count` occurrences and the rule in plain words.
    With `trigger=on_done` the dates assume each one is done on its due day, starting today.
    A rule Slate would refuse is a 422 with the reason."""
    return recurrences.preview(
        rrule,
        dtstart=dtstart,
        timezone=timezone,
        count=count,
        trigger=trigger,
        skip_weekends=skip_weekends,
    )


@router.get("/recurrences/upcoming", response_model=list[UpcomingOut])
def upcoming(
    _: ReadAuth,
    session: DbSession,
    start: date,
    end: date,
    list_id: str | None = None,
) -> list[dict]:
    """Dates scheduled rules will fill between `start` and `end` (YYYY-MM-DD, inclusive, at
    most 400 days) that are not items yet, for drawing future repeats on a calendar. Paused,
    ended and `on_done` rules have none."""
    return recurrences.upcoming(session, start=start, end=end, list_id=list_id)


@router.get("/recurrences/{rule_id}", response_model=RecurrenceOut)
def get_recurrence(rule_id: str, _: ReadAuth, session: DbSession) -> dict:
    return recurrences.recurrence_view(session, recurrences.get_recurrence(session, rule_id))


@router.post("/recurrences", status_code=status.HTTP_201_CREATED, response_model=RecurrenceOut)
def create_recurrence(body: RecurrenceIn, auth: WriteAuth, session: DbSession) -> dict:
    """Create a rule. Instances appear on the next scheduler tick (within a minute), or at once
    with `POST /recurrences/{id}/run-now`."""
    fields = body.model_dump(exclude_none=True)
    rule = recurrences.create_recurrence(session, auth.actor, **fields)
    return recurrences.recurrence_view(session, rule)


@router.patch("/recurrences/{rule_id}", response_model=RecurrenceOut)
def update_recurrence(
    rule_id: str, body: RecurrencePatch, auth: WriteAuth, session: DbSession
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    ended = changes.pop("ended", None)
    if ended is False:
        raise Invalid("an ended rule stays ended; create a new rule instead")
    rule = recurrences.update_recurrence(session, auth.actor, rule_id, changes)
    if ended:
        rule = recurrences.end_recurrence(session, auth.actor, rule_id)
    return recurrences.recurrence_view(session, rule)


@router.post("/recurrences/{rule_id}/pause", response_model=RecurrenceOut)
def pause_recurrence(rule_id: str, auth: WriteAuth, session: DbSession) -> dict:
    rule = recurrences.pause_recurrence(session, auth.actor, rule_id)
    return recurrences.recurrence_view(session, rule)


@router.post("/recurrences/{rule_id}/resume", response_model=RecurrenceOut)
def resume_recurrence(rule_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Resume from today; occurrences that fell inside the pause are not spawned."""
    rule = recurrences.resume_recurrence(session, auth.actor, rule_id)
    return recurrences.recurrence_view(session, rule)


@router.post("/recurrences/{rule_id}/run-now", response_model=RunNowOut)
def run_now(rule_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Materialize this rule now: everything due within its lead window, under its policy."""
    spawned = recurrences.run_now(session, auth.actor, rule_id)
    rule = recurrences.get_recurrence(session, rule_id)
    return {
        "recurrence": recurrences.recurrence_view(session, rule),
        "spawned": items.views(session, spawned),
    }


@router.post("/recurrences/{rule_id}/adopt", response_model=ItemOut)
def adopt(rule_id: str, body: AdoptIn, auth: WriteAuth, session: DbSession) -> dict:
    """Make an existing item this rule's current occurrence. A dated item keeps its due date;
    an undated one takes the rule's next occurrence. The rule spawns from the one after."""
    item = recurrences.adopt_instance(session, auth.actor, rule_id, body.item_id)
    return items.views(session, [item])[0]


def install(app: FastAPI) -> None:
    app.include_router(router)
