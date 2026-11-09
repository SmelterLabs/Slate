"""Reminders, the daily digest preview, and phone/desktop push subscriptions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from slate import mailer, webpush
from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.service import email, push, reminders
from slate.service.errors import Unavailable

router = APIRouter(tags=["reminders"])


class ReminderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: str | None = Field(default=None, description="When: ISO 8601, or YYYY-MM-DD (5 pm)")
    before_minutes: int | None = Field(
        default=None,
        ge=0,
        description="Or: this many minutes before the due date (0 = at it). Any number per "
        "item; the same lead for the same account twice returns the one already set. A "
        "date-only due date counts back from 17:00 workspace time",
    )
    account: str | None = Field(default=None, description="Who to remind (handle); default you")
    note: str | None = Field(default=None, max_length=280)


class ReminderOut(BaseModel):
    id: str
    item_id: str
    item_title: str | None
    account_id: str
    account: str | None
    remind_at: datetime | None
    before_minutes: int | None
    at: datetime | None = Field(description="When it goes off (null: before an unset due date)")
    note: str | None
    fired: bool
    created_by: str
    created_at: datetime


class DigestItem(BaseModel):
    id: str
    title: str
    due_at: str | None
    priority: str | None


class DigestAgentNext(BaseModel):
    item_id: str
    title: str
    list: str | None
    reason: str = Field(description="due, run_requested or ask (see GET /agents/plan)")
    at: str | None = Field(description="When it becomes takeable (null: now)")


class DigestAgent(BaseModel):
    id: str
    handle: str
    display_name: str
    next: list[DigestAgentNext]


class DigestOut(BaseModel):
    date: str
    counts: dict[str, int]
    overdue: list[DigestItem]
    today: list[DigestItem]
    waiting: list[DigestItem]
    agents: list[DigestAgent] = Field(
        default_factory=list,
        description="Agents today: what each agent runs by the end of the day (only when "
        "some agent has something)",
    )
    enabled: bool = Field(description="Whether the digest is on for you (prefs `digest`)")
    hour: int = Field(description="Hour it arrives, workspace time (prefs `digest_hour`)")


class PushKeyOut(BaseModel):
    enabled: bool
    public_key: str | None = Field(description="VAPID application server key (base64url)")


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(max_length=2000)
    keys: PushKeys
    label: str | None = Field(default=None, max_length=100, description="e.g. 'Phone'")


class PushOut(BaseModel):
    id: str
    label: str | None
    endpoint_host: str
    created_at: datetime
    last_ok_at: datetime | None
    failures: int


class PushTestOut(PushOut):
    ok: bool
    error: str | None


class EmailTestOut(BaseModel):
    ok: bool
    error: str | None = None


class EndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str


@router.get("/items/{item_id}/reminders", response_model=list[ReminderOut])
def item_reminders(item_id: str, auth: ReadAuth, session: DbSession) -> list[dict]:
    return reminders.reminders_of(session, item_id)


@router.post(
    "/items/{item_id}/reminders", status_code=status.HTTP_201_CREATED, response_model=ReminderOut
)
def add_reminder(item_id: str, body: ReminderIn, auth: WriteAuth, session: DbSession) -> dict:
    r = reminders.add_reminder(session, auth.actor, item_id, **body.model_dump())
    return reminders.reminder_view(session, r)


@router.delete("/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_reminder(reminder_id: str, auth: WriteAuth, session: DbSession) -> Response:
    reminders.remove_reminder(session, auth.actor, reminder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/reminders", response_model=list[ReminderOut])
def my_reminders(
    auth: ReadAuth, session: DbSession, limit: int = Query(default=50, ge=1, le=500)
) -> list[dict]:
    """Your reminders that have not gone off yet, soonest first."""
    return reminders.upcoming(session, auth.account.id, limit=limit)


@router.get("/me/digest", response_model=DigestOut)
def my_digest(auth: ReadAuth, session: DbSession) -> dict[str, Any]:
    """What your daily digest says right now (it arrives once a day at your hour)."""
    on, hour = reminders.digest_settings(auth.account)
    return {**reminders.digest_for(session, auth.account), "enabled": on, "hour": hour}


@router.get("/push/key", response_model=PushKeyOut)
def push_key(auth: ReadAuth) -> dict:
    """The key a browser subscribes with; `enabled` is false when the server cannot push."""
    if not webpush.enabled():
        return {"enabled": False, "public_key": None}
    return {"enabled": True, "public_key": webpush.public_key()}


@router.get("/push/subscriptions", response_model=list[PushOut])
def my_devices(auth: ReadAuth, session: DbSession) -> list[dict]:
    return [push.subscription_view(s) for s in push.list_subscriptions(session, auth.account.id)]


@router.post("/push/subscriptions", status_code=status.HTTP_201_CREATED, response_model=PushOut)
def subscribe(body: PushIn, auth: WriteAuth, session: DbSession) -> dict:
    if not webpush.enabled():
        raise Unavailable("push is off: the server has no SLATE_SECRET_KEY")
    sub = push.subscribe(
        session,
        auth.actor,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        label=body.label,
    )
    return push.subscription_view(sub)


@router.delete("/push/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(subscription_id: str, auth: WriteAuth, session: DbSession) -> Response:
    push.unsubscribe(session, auth.actor, subscription_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/push/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe_endpoint(body: EndpointIn, auth: WriteAuth, session: DbSession) -> Response:
    """Turn off this browser by its endpoint (what the board knows about itself)."""
    push.unsubscribe_endpoint(session, auth.actor, body.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/push/test", response_model=list[PushTestOut])
def push_test(auth: WriteAuth, session: DbSession) -> list[dict]:
    """Send a test notification to each of your devices now."""
    if not webpush.enabled():
        raise Unavailable("push is off: the server has no SLATE_SECRET_KEY")
    return push.send_test(session, auth.account.id, webpush.send)


@router.post("/email/test", response_model=EmailTestOut)
def email_test(auth: WriteAuth, session: DbSession) -> EmailTestOut:
    """Email yourself a test notification now, to the address in your prefs (`email_to`)."""
    if not mailer.enabled():
        raise Unavailable("email is off: the server has no SLATE_SMTP_HOST and SLATE_SMTP_FROM")
    error = email.send_test(auth.account, mailer.send)
    return EmailTestOut(ok=error is None, error=error)
