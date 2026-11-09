"""Webhook subscriptions under /api/v1/webhooks. Every route needs `write`.

The plaintext secret is in the POST response and nowhere else; no other route returns it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from slate import notify
from slate.api.auth import DbSession, WriteAuth
from slate.models import Delivery, Webhook
from slate.service import webhooks

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookFilter(BaseModel):
    assignee_me: bool = False
    section_ids: list[str] = Field(default_factory=list)
    list_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    due_lead_hours: int = Field(default=webhooks.DEFAULT_LEAD_HOURS)


class WebhookIn(BaseModel):
    url: str = Field(max_length=2000)
    events: list[str] | Literal["*"] = Field(
        description="Event types (e.g. item.due, item.assigned) or ['*'] for all but webhook.*"
    )
    filter: WebhookFilter | None = None


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    events: list[str] | Literal["*"] | None = None
    filter: WebhookFilter | None = None
    disabled: bool | None = None


class WebhookOut(BaseModel):
    id: str
    account_id: str
    url: str
    events: list[str]
    filter: dict[str, Any]
    created_at: datetime
    disabled_at: datetime | None


class WebhookCreatedOut(WebhookOut):
    """Includes the plaintext `secret`. This is the only response that ever does."""

    secret: str


class DeliveryOut(BaseModel):
    id: str
    webhook_id: str
    event_id: str
    attempt: int
    state: Literal["pending", "sending", "ok", "retry", "failed", "skipped"]
    status_code: int | None
    response_ms: int | None
    error: str | None
    at: datetime
    next_at: datetime | None


class DeliveryPage(BaseModel):
    deliveries: list[DeliveryOut]
    next_offset: int | None


def _out(hook: Webhook) -> WebhookOut:
    return WebhookOut(**webhooks.view(hook))


def _delivery(d: Delivery) -> DeliveryOut:
    return DeliveryOut(
        id=d.id,
        webhook_id=d.webhook_id,
        event_id=d.event_id,
        attempt=d.attempt,
        state=d.state,
        status_code=d.status_code,
        response_ms=d.response_ms,
        error=d.error,
        at=d.at,
        next_at=d.next_at,
    )


@router.get("", response_model=list[WebhookOut])
def list_webhooks(auth: WriteAuth, session: DbSession) -> list[WebhookOut]:
    """Your own subscriptions (an admin sees everyone's)."""
    return [_out(h) for h in webhooks.list_webhooks(session, auth.principal)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WebhookCreatedOut)
def create_webhook(body: WebhookIn, auth: WriteAuth, session: DbSession) -> WebhookCreatedOut:
    """Subscribe. Store the returned `secret` now: it is never shown again."""
    hook, secret = webhooks.create_webhook(
        session,
        auth.actor,
        url=body.url,
        events=body.events,
        filter=body.filter.model_dump() if body.filter else None,
    )
    return WebhookCreatedOut(**webhooks.view(hook), secret=secret)


@router.get("/{webhook_id}", response_model=WebhookOut)
def get_webhook(webhook_id: str, auth: WriteAuth, session: DbSession) -> WebhookOut:
    return _out(webhooks.get_webhook(session, webhook_id, by=auth.principal))


@router.patch("/{webhook_id}", response_model=WebhookOut)
def update_webhook(
    webhook_id: str, body: WebhookPatch, auth: WriteAuth, session: DbSession
) -> WebhookOut:
    """Change url, events, filter (replaced whole), or `disabled: true|false`."""
    changes = body.model_dump(exclude_unset=True)
    if body.filter is not None:
        changes["filter"] = body.filter.model_dump()
    hook = webhooks.update_webhook(session, auth.actor, webhook_id, changes, by=auth.principal)
    return _out(hook)


@router.get("/{webhook_id}/deliveries", response_model=DeliveryPage)
def list_deliveries(
    webhook_id: str,
    auth: WriteAuth,
    session: DbSession,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> DeliveryPage:
    """Every attempt, newest first. Page with `offset`; `next_offset` is null on the last page."""
    rows = webhooks.list_deliveries(
        session, webhook_id, by=auth.principal, limit=limit + 1, offset=offset
    )
    more = len(rows) > limit
    return DeliveryPage(
        deliveries=[_delivery(d) for d in rows[:limit]],
        next_offset=offset + limit if more else None,
    )


@router.post("/{webhook_id}/test", response_model=DeliveryOut)
def test_webhook(webhook_id: str, auth: WriteAuth, session: DbSession) -> DeliveryOut:
    """Send a `webhook.test` delivery now (one attempt, no retries) and return its outcome."""
    delivery = webhooks.create_test_delivery(session, auth.actor, webhook_id, by=auth.principal)
    result = notify.attempt(delivery.id, retry=False)
    return _delivery(result)
