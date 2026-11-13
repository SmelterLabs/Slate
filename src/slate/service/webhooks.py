"""Webhook subscriptions: who wants which events, where, and the secret deliveries are signed with.

A subscription belongs to the account that created it; that account (or an admin) manages it.
The secret (`whsec_...`) is generated here, returned once by `create_webhook()`, and stored
only encrypted (`slate.crypto`), never in an event or a response. Delivery itself lives in
`slate.service.deliveries` (the queue) and `slate.notify` (the HTTP sender).

Fields:
- `url`: https anywhere; plain http only to a private address (see `validate_url`).
- `events_json`: event types (`item.due`, ...) or `["*"]` (every type except `webhook.*`).
- `filter_json`: `{assignee_me?, section_ids?, list_ids?, tags?, due_lead_hours}`; all
  present conditions must hold; `due_lead_hours` (default 24) picks which `item.due_soon`.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import socket
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate import crypto
from slate.ids import new_id
from slate.models import Delivery, Event, Webhook, utcnow
from slate.service import tags as tags_svc
from slate.service.accounts import Principal
from slate.service.core import Actor, apply_change
from slate.service.errors import Invalid, NotFound, Unavailable
from slate.settings import settings

EVENT_TYPE_RE = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")
DEFAULT_LEAD_HOURS = 24
MAX_LEAD_HOURS = 24 * 30
SECRET_PREFIX = "whsec_"
# Name suffixes that only resolve inside a private network.
_PRIVATE_SUFFIXES = (".ts.net", ".local", ".internal", ".lan", ".home.arpa")
_UPDATABLE = {"url", "events", "filter", "disabled"}


# --- Validation ----------------------------------------------------------------------------


def _private_host(host: str) -> bool:
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    host = host.lower().rstrip(".")
    return host == "localhost" or "." not in host or host.endswith(_PRIVATE_SUFFIXES)


_PRIVATE_REFUSED = (
    "url points at a private address, which this Slate does not allow (set "
    "SLATE_WEBHOOK_PRIVATE_TARGETS=true for a receiver on this host or network)"
)


def validate_url(url: str) -> str:
    """https to any public host. Private targets (a private IP, localhost, a bare name, a
    tailnet or .local name) only with `SLATE_WEBHOOK_PRIVATE_TARGETS`, and then over plain
    http too. Judged by the name here; `check_target()` resolves it at send time."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Invalid("url must be an absolute http(s) URL")
    if len(url) > 2000:
        raise Invalid("url is longer than 2000 characters")
    private = _private_host(parts.hostname)
    if private and not settings.webhook_private_targets:
        raise Invalid(_PRIVATE_REFUSED)
    if parts.scheme == "http" and not private:
        raise Invalid("plain http is allowed only to private addresses; use https")
    return url


def private_targets(session: Session) -> list[dict[str, Any]]:
    """Every live webhook and enabled automation `webhook` action whose URL names a private
    address (the ones `SLATE_WEBHOOK_PRIVATE_TARGETS=false` refuses). For `slate
    check-targets`, run before upgrading a deployment."""
    from slate.models import Account, Automation

    out: list[dict[str, Any]] = []
    for hook in session.scalars(
        select(Webhook).where(Webhook.disabled_at.is_(None)).order_by(Webhook.created_at)
    ):
        if _private_host(urlsplit(hook.url).hostname or ""):
            owner = session.get(Account, hook.account_id)
            out.append(
                {
                    "kind": "webhook",
                    "id": hook.id,
                    "name": owner.handle if owner else hook.account_id,
                    "url": hook.url,
                }
            )
    for rule in session.scalars(
        select(Automation).where(Automation.enabled.is_(True)).order_by(Automation.created_at)
    ):
        for action in rule.actions_json or []:
            url = action.get("url") if action.get("type") == "webhook" else None
            if url and _private_host(urlsplit(url).hostname or ""):
                out.append({"kind": "automation", "id": rule.id, "name": rule.name, "url": url})
    return out


class LookupFailed(OSError):
    """The target's name did not resolve: a network error, retried like one."""


def lookup(host: str) -> list[str]:
    """Every address `host` resolves to, in the resolver's order (an IP literal is itself)."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def check_target(url: str) -> list[str]:
    """Send-time check of a validated URL; returns the addresses to connect to.

    The name is resolved here, once, and the sender connects to exactly these addresses
    (`slate.outbound`), so a name that answers differently a second time cannot reach another
    host. With private targets off, the host must still be public *and* every address it
    resolves to must be public (a name can be pointed at 127.0.0.1 after it was saved):
    `Invalid` otherwise. A lookup that fails raises `LookupFailed` (fail closed)."""
    host = urlsplit(url).hostname or ""
    if not settings.webhook_private_targets and _private_host(host):
        raise Invalid(_PRIVATE_REFUSED)
    try:
        addresses = lookup(host)
    except (OSError, UnicodeError) as exc:  # UnicodeError: a name IDNA cannot encode
        raise LookupFailed(f"could not resolve {host}: {exc}") from None
    if not addresses:
        raise LookupFailed(f"could not resolve {host}")
    if not settings.webhook_private_targets:
        for address in addresses:
            # A link-local IPv6 answer carries %scope.
            if not ipaddress.ip_address(address.split("%")[0]).is_global:
                raise Invalid(
                    f"{host} resolves to a private address, which this Slate does not allow"
                )
    return addresses


def _events(value: Any) -> list[str]:
    if value == "*":
        value = ["*"]
    if not isinstance(value, list | tuple) or not value:
        raise Invalid("events must be a non-empty list of event types, or ['*']")
    out: list[str] = []
    for t in value:
        if not isinstance(t, str) or not (t == "*" or EVENT_TYPE_RE.match(t)):
            raise Invalid(f"bad event type {t!r}: expected e.g. 'item.due' or '*'")
        if t not in out:
            out.append(t)
    return ["*"] if "*" in out else out


def _str_list(value: Any, key: str) -> list[str]:
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        raise Invalid(f"filter.{key} must be a list of strings")
    return list(dict.fromkeys(value))


def _filter(value: Mapping[str, Any] | None) -> dict[str, Any]:
    value = dict(value or {})
    unknown = set(value) - {"assignee_me", "section_ids", "list_ids", "tags", "due_lead_hours"}
    if unknown:
        raise Invalid(f"unknown filter keys: {sorted(unknown)}")
    out: dict[str, Any] = {}
    me = value.get("assignee_me", False)
    if not isinstance(me, bool):
        raise Invalid("filter.assignee_me must be true or false")
    if me:
        out["assignee_me"] = True
    for key in ("section_ids", "list_ids"):
        if value.get(key):
            out[key] = _str_list(value[key], key)
    if value.get("tags"):
        out["tags"] = list(
            dict.fromkeys(tags_svc.normalize(t) for t in _str_list(value["tags"], "tags"))
        )
    lead = value.get("due_lead_hours", DEFAULT_LEAD_HOURS)
    if isinstance(lead, bool) or not isinstance(lead, int) or not 1 <= lead <= MAX_LEAD_HOURS:
        raise Invalid(f"filter.due_lead_hours must be a whole number of hours, 1-{MAX_LEAD_HOURS}")
    out["due_lead_hours"] = lead
    return out


def lead_hours(webhook: Webhook) -> int:
    return (webhook.filter_json or {}).get("due_lead_hours", DEFAULT_LEAD_HOURS)


def wants_type(webhook: Webhook, event_type: str) -> bool:
    """Does the subscription's event list cover this type? `*` covers all but `webhook.*`."""
    events = webhook.events_json or []
    if event_type in events:
        return True
    return "*" in events and not event_type.startswith("webhook.")


def _encrypt(secret: str, webhook_id: str) -> str:
    try:
        return crypto.encrypt(secret, aad=webhook_id)
    except crypto.SecretKeyError as exc:
        raise Unavailable(str(exc)) from None


# --- Reads ---------------------------------------------------------------------------------


def view(webhook: Webhook) -> dict[str, Any]:
    """The public shape of a subscription. Never includes the secret, in any form."""
    return {
        "id": webhook.id,
        "account_id": webhook.account_id,
        "url": webhook.url,
        "events": webhook.events_json,
        "filter": webhook.filter_json or {},
        "created_at": webhook.created_at,
        "disabled_at": webhook.disabled_at,
    }


def list_webhooks(session: Session, by: Principal) -> list[Webhook]:
    """The caller's own subscriptions; an admin sees everyone's."""
    q = select(Webhook).order_by(Webhook.created_at, Webhook.id)
    if not by.has("admin"):
        q = q.where(Webhook.account_id == by.account.id)
    return list(session.scalars(q))


def get_webhook(session: Session, webhook_id: str, *, by: Principal) -> Webhook:
    """404 for someone else's subscription, so ids do not leak across accounts."""
    webhook = session.get(Webhook, webhook_id)
    if webhook is None or not (by.has("admin") or webhook.account_id == by.account.id):
        raise NotFound(f"webhook {webhook_id} not found")
    return webhook


def list_deliveries(
    session: Session, webhook_id: str, *, by: Principal, limit: int = 50, offset: int = 0
) -> list[Delivery]:
    """Delivery attempts, newest first (one row per attempt; pending rows included)."""
    get_webhook(session, webhook_id, by=by)
    return list(
        session.scalars(
            select(Delivery)
            .where(Delivery.webhook_id == webhook_id)
            .order_by(Delivery.at.desc(), Delivery.attempt.desc(), Delivery.id)
            .limit(limit)
            .offset(offset)
        )
    )


# --- Writes --------------------------------------------------------------------------------


def create_webhook(
    session: Session,
    actor: Actor,
    *,
    url: str,
    events: Any,
    filter: Mapping[str, Any] | None = None,
) -> tuple[Webhook, str]:
    """Subscribe the actor's account. Returns (row, plaintext secret); shown once, never again."""
    webhook = Webhook(
        id=new_id(),
        account_id=actor.account_id,
        url=validate_url(url),
        events_json=_events(events),
        filter_json=_filter(filter),
    )
    secret = SECRET_PREFIX + secrets.token_urlsafe(32)
    webhook.secret_enc = _encrypt(secret, webhook.id)
    with apply_change(session, actor) as cx:
        cx.create(webhook, "webhook.created")
    return webhook, secret


def update_webhook(
    session: Session,
    actor: Actor,
    webhook_id: str,
    changes: Mapping[str, Any],
    *,
    by: Principal,
) -> Webhook:
    """Change `url`, `events`, `filter` (replaced whole) and/or `disabled` (true/false)."""
    unknown = set(changes) - _UPDATABLE
    if unknown:
        raise Invalid(f"cannot update {sorted(unknown)}")
    webhook = get_webhook(session, webhook_id, by=by)
    new: dict[str, Any] = {}
    if "url" in changes:
        new["url"] = validate_url(changes["url"])
    if "events" in changes:
        new["events_json"] = _events(changes["events"])
    if "filter" in changes:
        new["filter_json"] = _filter(changes["filter"])
    with apply_change(session, actor) as cx:
        for column, value in new.items():
            cx.set(webhook, column, value, "webhook.updated")
        if "disabled" in changes:
            if changes["disabled"] and webhook.disabled_at is None:
                cx.set(webhook, "disabled_at", utcnow(), "webhook.disabled")
            elif not changes["disabled"] and webhook.disabled_at is not None:
                cx.set(webhook, "disabled_at", None, "webhook.enabled")
    return webhook


def disable_webhook(session: Session, actor: Actor, webhook_id: str, *, by: Principal) -> Webhook:
    return update_webhook(session, actor, webhook_id, {"disabled": True}, by=by)


def enable_webhook(session: Session, actor: Actor, webhook_id: str, *, by: Principal) -> Webhook:
    return update_webhook(session, actor, webhook_id, {"disabled": False}, by=by)


def create_test_delivery(
    session: Session, actor: Actor, webhook_id: str, *, by: Principal, now: datetime | None = None
) -> Delivery:
    """Record a `webhook.test` event and a delivery for this one subscription, already claimed.

    The caller sends it straight away (`slate.notify.attempt(..., retry=False)`); a crash
    before that leaves it `sending`, which the dispatcher re-queues on start.
    """
    from slate.service import deliveries  # deliveries imports this module

    webhook = get_webhook(session, webhook_id, by=by)
    if webhook.disabled_at is not None:
        raise Invalid("webhook is disabled; enable it before testing")
    with apply_change(session, actor) as cx:
        ev: Event = cx.record(
            "webhook.test",
            subject_id=webhook.id,
            after={"webhook_id": webhook.id, "url": webhook.url},
        )
    delivery = Delivery(
        webhook_id=webhook.id,
        event_id=ev.id,
        attempt=1,
        state="sending",
        next_at=now or utcnow(),
        body=deliveries.delivery_body(session, ev),
    )
    session.add(delivery)
    session.commit()
    return delivery
