"""The webhook delivery queue, kept in the `deliveries` table so a restart resumes it.

One row per attempt. Life of a delivery:

    fan_out()        event committed -> one `pending` row per matching subscription (attempt 1)
    claim_due()      pending rows whose `next_at` has come -> `sending`
    prepare_attempt  load url, body and decrypted secret (or `skipped` if the hook is disabled)
    record_attempt() `ok` | `failed` (final) | `retry` + a new `pending` row (attempt n+1)
    recover_claimed  on start: `sending` rows left by a crash -> `pending` (at-least-once)
    prune()          finished rows older than `settings.delivery_retention_days` -> deleted

These rows are bookkeeping (`core.BOOKKEEPING_MODELS`): the table is itself the delivery log,
so writing it records no event. The HTTP side is `slate.notify`.

Body: the flat `core.event_payload()` shape the SSE stream sends, plus `item` (a compact
summary as the item is at fan-out, or null) and `actor` (`{id, handle, kind}`). Stored as the
exact JSON text that is signed, so every retry sends identical bytes. It is stored once per
(webhook, event): a retry hands it to the next attempt's row and keeps `""`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from slate import crypto
from slate.models import Account, Delivery, Event, Item, ItemAssignee, List, Watcher, Webhook
from slate.service import access, webhooks
from slate.service import items as items_svc
from slate.service.core import event_payload, jsonable
from slate.settings import settings

log = logging.getLogger(__name__)

BATCH = 500
ERROR_MAX = 500


@dataclass
class _Context:
    item: Item | None = None
    section_id: str | None = None
    assignee_ids: set[str] = field(default_factory=set)
    watcher_ids: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Attempt:
    delivery_id: str
    url: str
    body: str
    secret: str
    event_type: str


# --- Body ----------------------------------------------------------------------------------


def item_summary(session: Session, item_id: str | None) -> dict[str, Any] | None:
    item = session.get(Item, item_id) if item_id else None
    if item is None:
        return None
    [v] = items_svc.views(session, [item])
    return jsonable(
        {
            "id": v["id"],
            "title": v["title"],
            "list_id": v["list_id"],
            "status": v["status"]["name"] if v["status"] else None,
            "priority": v["priority"],
            "due_at": v["due_at"],
            "assignees": [a["handle"] for a in v["assignees"]],
            "tags": v["tags"],
        }
    )


def delivery_body(session: Session, ev: Event) -> str:
    actor = session.get(Account, ev.actor_id)
    body = event_payload(ev)
    body["item"] = item_summary(session, ev.item_id)
    body["actor"] = (
        {"id": actor.id, "handle": actor.handle, "kind": jsonable(actor.kind)} if actor else None
    )
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


# --- Matching ------------------------------------------------------------------------------


def _context(session: Session, ev: Event) -> _Context:
    item = session.get(Item, ev.item_id) if ev.item_id else None
    if item is None:
        return _Context()
    view = items_svc.views(session, [item])[0]
    return _Context(
        item=item,
        section_id=session.scalar(select(List.section_id).where(List.id == item.list_id)),
        assignee_ids=set(
            session.scalars(select(ItemAssignee.account_id).where(ItemAssignee.item_id == item.id))
        ),
        watcher_ids=set(
            session.scalars(select(Watcher.account_id).where(Watcher.item_id == item.id))
        ),
        tags=set(view["tags"]),
    )


def matches(webhook: Webhook, ev: Event, ctx: _Context, owner_handle: str | None) -> bool:
    """Type and filter match. Item conditions are judged on the item as it is at fan-out."""
    if webhook.disabled_at is not None or not webhooks.wants_type(webhook, ev.type):
        return False
    f = webhook.filter_json or {}
    if ev.type == "item.due_soon":
        lead = (ev.after_json or {}).get("lead_hours")
        if lead != f.get("due_lead_hours", webhooks.DEFAULT_LEAD_HOURS):
            return False
    item_scoped = any(f.get(k) for k in ("assignee_me", "section_ids", "list_ids", "tags"))
    if not item_scoped:
        return True
    if ev.type == "account.digest":
        # A digest has no item: it is "about me" only for the account it was made for.
        return bool(f.get("assignee_me")) and ev.subject_id == webhook.account_id
    if ev.type == "doc.mentioned":
        # A doc page has no item: it is "about me" when it mentions me.
        after = ev.after_json if isinstance(ev.after_json, dict) else {}
        return bool(f.get("assignee_me")) and owner_handle in (after.get("mentions") or [])
    if ev.type.startswith("message."):
        # A chat message has no item: it is "about me" when it mentions me or replies to me.
        after = ev.after_json if isinstance(ev.after_json, dict) else {}
        return bool(f.get("assignee_me")) and (
            owner_handle in (after.get("mentions") or [])
            or webhook.account_id in (after.get("thread_followers") or [])
        )
    if ctx.item is None:
        return False
    if f.get("assignee_me"):
        # Being unassigned is news to the one unassigned, so `before` counts for item.assigned.
        was = ev.type == "item.assigned" and owner_handle in (ev.before_json or [])
        # Watching an item, or being @mentioned in a comment on it, counts as "about me" too.
        mentioned = ev.type in ("item.commented", "item.mentioned") and owner_handle in (
            (ev.after_json or {}).get("mentions") or []
        )
        reminded = (
            ev.type == "item.reminder"
            and (ev.after_json or {}).get("account_id") == webhook.account_id
        )
        involved = webhook.account_id in ctx.assignee_ids | ctx.watcher_ids or reminded
        if not (involved or was or mentioned):
            return False
    if f.get("section_ids") and ctx.section_id not in f["section_ids"]:
        return False
    if f.get("list_ids") and ctx.item.list_id not in f["list_ids"]:
        return False
    return not (f.get("tags") and not ctx.tags & set(f["tags"]))


# --- Queue ---------------------------------------------------------------------------------


def latest_event_id(session: Session) -> str | None:
    return session.scalar(select(func.max(Event.id)))


def _rows_for(
    session: Session,
    ev: Event,
    hooks: list[Webhook],
    handles: dict[str, str],
    gates: dict[str, access.EventGate],
    now: datetime,
) -> list[Delivery]:
    """The new delivery rows for one event (not yet added)."""
    wanted = [h for h in hooks if webhooks.wants_type(h, ev.type)]
    if not wanted:
        return []
    ctx = _context(session, ev)
    targets = [h for h in wanted if matches(h, ev, ctx, handles.get(h.account_id))]
    if targets:
        payload = event_payload(ev)
        for h in targets:
            gate = gates.get(h.account_id)
            if gate is None or gate.needs_reload(payload):
                gate = gates[h.account_id] = access.EventGate(h.account_id)
                gate.load(session)
        targets = [h for h in targets if gates[h.account_id].allows(session, payload)]
    if not targets:
        return []
    body = delivery_body(session, ev)
    return [
        Delivery(webhook_id=h.id, event_id=ev.id, attempt=1, next_at=now, body=body)
        for h in targets
    ]


def fan_out(session: Session, cursor: str | None, *, now: datetime) -> tuple[str | None, int]:
    """Queue a delivery for each (event after `cursor`, matching subscription). Commits.

    Returns (new cursor, rows queued). Reads the events table, not the bus, so nothing a
    slow consumer drops is missed.
    """
    queued = 0
    while True:
        q = select(Event).order_by(Event.id).limit(BATCH)
        if cursor is not None:
            q = q.where(Event.id > cursor)
        batch = list(session.scalars(q))
        if not batch:
            return cursor, queued
        hooks = list(session.scalars(select(Webhook).where(Webhook.disabled_at.is_(None))))
        handles = dict(
            session.execute(
                select(Account.id, Account.handle).where(
                    Account.id.in_({h.account_id for h in hooks})
                )
            ).all()
        )
        # Permissions: a webhook only hears what its owner could read (service/access.py).
        gates: dict[str, access.EventGate] = {}
        for ev in batch:
            try:
                rows = _rows_for(session, ev, hooks, handles, gates, now)
            except SQLAlchemyError:
                raise  # the database, not this event: the dispatcher retries the step
            except Exception:
                # One event that cannot be summarised must not stop every event after it.
                log.exception("webhooks: skipping event %s, which could not be queued", ev.id)
                continue
            session.add_all(rows)
            queued += len(rows)
        session.commit()
        cursor = batch[-1].id
        if len(batch) < BATCH:
            return cursor, queued


def claim_due(session: Session, *, now: datetime, limit: int) -> list[str]:
    """Mark up to `limit` due pending rows `sending` and return their ids, oldest first."""
    rows = list(
        session.scalars(
            select(Delivery)
            .where(Delivery.state == "pending", Delivery.next_at <= now)
            .order_by(Delivery.next_at, Delivery.id)
            .limit(limit)
        )
    )
    for row in rows:
        row.state = "sending"
    session.commit()
    return [r.id for r in rows]


def recover_claimed(session: Session) -> int:
    """After a restart: deliveries claimed but never recorded go back to the queue."""
    rows = list(session.scalars(select(Delivery).where(Delivery.state == "sending")))
    for row in rows:
        row.state = "pending"
    session.commit()
    return len(rows)


def _finish(session: Session, row: Delivery, state: str, error: str, now: datetime) -> None:
    row.state, row.error, row.at, row.next_at = state, error[:ERROR_MAX], now, None
    session.commit()


def prepare_attempt(session: Session, delivery_id: str, *, now: datetime) -> Attempt | None:
    """What to send, or None if the row is not claimable or must not go out (row updated)."""
    row = session.get(Delivery, delivery_id)
    if row is None or row.state != "sending":
        return None
    webhook = session.get(Webhook, row.webhook_id)
    if webhook is None or webhook.disabled_at is not None:
        _finish(session, row, "skipped", "webhook disabled before sending", now)
        return None
    # A queued digest can outlive the grants that authorized its nested snapshots.
    # Keep webhook retry bytes immutable: suppress, rather than rewrite, unsafe snapshots.
    payload = json.loads(row.body)
    if payload.get("type") == "account.digest":
        gate = access.EventGate(webhook.account_id)
        gate.load(session)
        if not gate.allows(session, payload):
            _finish(session, row, "skipped", "digest access changed before sending", now)
            return None
    try:
        secret = crypto.decrypt(webhook.secret_enc, aad=webhook.id)
    except crypto.SecretKeyError as exc:
        _finish(session, row, "failed", str(exc), now)
        return None
    event_type = session.scalar(select(Event.type).where(Event.id == row.event_id))
    return Attempt(row.id, webhook.url, row.body, secret, event_type or "")


def retryable(status_code: int | None) -> bool:
    """Network errors (no status), 5xx and 429 retry; any other non-2xx is final."""
    return status_code is None or status_code == 429 or status_code >= 500


def record_attempt(
    session: Session,
    delivery_id: str,
    *,
    status_code: int | None,
    response_ms: int | None,
    error: str | None,
    now: datetime,
    retry_delays: Sequence[float],
    retry: bool = True,
) -> Delivery:
    """Log one attempt's outcome and, if it may retry, queue the next attempt."""
    row = session.get(Delivery, delivery_id)
    row.status_code, row.response_ms, row.at, row.next_at = status_code, response_ms, now, None
    row.error = error[:ERROR_MAX] if error else None
    if status_code is not None and 200 <= status_code < 300:
        row.state = "ok"
    elif retry and retryable(status_code) and row.attempt <= len(retry_delays):
        row.state = "retry"
        # The signed bytes move to the next attempt; this row keeps the outcome only.
        session.add(
            Delivery(
                webhook_id=row.webhook_id,
                event_id=row.event_id,
                attempt=row.attempt + 1,
                state="pending",
                next_at=now + timedelta(seconds=retry_delays[row.attempt - 1]),
                body=row.body,
                at=now,
            )
        )
        row.body = ""
    else:
        row.state = "failed"
    session.commit()
    return row


# States with nothing left to send: the row is history. A `retry` row's later attempt is its
# own row, so it counts too.
FINISHED = ("ok", "failed", "skipped", "retry")


def prune(session: Session, *, now: datetime, limit: int = BATCH) -> int:
    """Delete up to `limit` finished rows older than `settings.delivery_retention_days`
    (oldest first; 0 keeps everything). Queued (`pending`, `sending`) rows are never pruned,
    so the Health page's queue counts and a restart's recovery are untouched."""
    days = settings.delivery_retention_days
    if days <= 0:
        return 0
    # Row by row, not a bulk DELETE: the write guard lets bookkeeping rows through only on
    # flush (see automations._prune).
    old = list(
        session.scalars(
            select(Delivery)
            .where(Delivery.state.in_(FINISHED), Delivery.at < now - timedelta(days=days))
            .order_by(Delivery.at)
            .limit(limit)
        )
    )
    for row in old:
        session.delete(row)
    if old:
        session.commit()
    return len(old)
