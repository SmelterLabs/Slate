"""The agents roster: who is around, what each is on, how busy each has been.

Reads only. Presence (last request, open live streams) is in memory (`slate.presence`) and
falls back to the newest token `last_used_at`; everything else comes from the database.
`plan()` is the "about to" view: what each agent will run in the next day, read with the work
loop's own eligibility rule, for the Agents page, the daily digest and `GET /agents/plan`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate import dates
from slate.models import (
    SYSTEM_HANDLE,
    Account,
    AccountKind,
    AgentState,
    Event,
    Item,
    ItemAssignee,
    List,
    Status,
    StatusKind,
    Token,
    utcnow,
)
from slate.presence import presence
from slate.service import access

ONLINE_WINDOW = timedelta(minutes=2)
ACTIVITY_WINDOW = timedelta(hours=24)


def roster(
    session: Session,
    *,
    kind: str | None = "agent",
    now: datetime | None = None,
    throttled: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """One entry per account (agents only by default; `kind=None` for everyone), most recently
    active first: presence, current reports, open assignments, last-24h event count."""
    now = now or utcnow()
    q = select(Account).where(Account.disabled_at.is_(None))
    if kind is not None:
        q = q.where(Account.kind == AccountKind(kind))
    visible = access.participant_ids(session)
    if visible is not None:
        q = q.where(Account.id.in_(visible))
    accounts = list(session.scalars(q))
    if not accounts:
        return []
    ids = [a.id for a in accounts]

    token_seen = dict(
        session.execute(
            select(Token.account_id, func.max(Token.last_used_at))
            .where(Token.account_id.in_(ids), Token.revoked_at.is_(None))
            .group_by(Token.account_id)
        ).all()
    )
    since = now - ACTIVITY_WINDOW
    events_24h = dict(
        session.execute(
            select(Event.actor_id, func.count())
            .where(Event.actor_id.in_(ids), Event.at >= since)
            .group_by(Event.actor_id)
        ).all()
    )
    last_event = dict(
        session.execute(
            select(Event.actor_id, func.max(Event.id))
            .where(Event.actor_id.in_(ids))
            .group_by(Event.actor_id)
        ).all()
    )
    last_event_at = {
        ev.actor_id: ev.at
        for ev in session.scalars(select(Event).where(Event.id.in_(list(last_event.values()))))
    }
    open_assigned = dict(
        session.execute(
            select(ItemAssignee.account_id, func.count())
            .join(Item, Item.id == ItemAssignee.item_id)
            .outerjoin(Status, Status.id == Item.status_id)
            .where(
                ItemAssignee.account_id.in_(ids),
                Item.archived_at.is_(None),
                Item.completed_at.is_(None),
                (Status.kind.is_(None))
                | Status.kind.not_in([StatusKind.done, StatusKind.cancelled]),
            )
            .group_by(ItemAssignee.account_id)
        ).all()
    )
    working: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
    for item in session.scalars(
        select(Item)
        .where(
            Item.agent_state_by.in_(ids),
            Item.agent_state.is_not(None),
            Item.archived_at.is_(None),
            Item.trashed_at.is_(None),
            # A closed card needs nobody: same rule as Needs you (web/src/lib/attention.ts).
            ~Item.status_id.in_(
                select(Status.id).where(Status.kind.in_([StatusKind.done, StatusKind.cancelled]))
            ),
        )
        .order_by(Item.agent_state_at.desc())
    ):
        working[item.agent_state_by].append(
            {
                "item_id": item.id,
                "title": item.title,
                "list_id": item.list_id,
                "state": item.agent_state.value,
                "note": item.agent_note,
                "progress": item.agent_progress,
                "url": item.agent_url,
                "at": item.agent_state_at,
            }
        )

    out = []
    for a in accounts:
        live = presence.last_seen(a.id)
        candidates = [t for t in (live.at if live else None, token_seen.get(a.id)) if t]
        last_seen = max(candidates) if candidates else None
        streams = presence.streams(a.id)
        out.append(
            {
                "account": {
                    "id": a.id,
                    "handle": a.handle,
                    "display_name": a.display_name,
                    "kind": a.kind.value,
                },
                "last_seen_at": last_seen,
                "last_seen_via": live.source if live else None,
                "online": bool(streams)
                or (last_seen is not None and now - last_seen <= ONLINE_WINDOW),
                "live_streams": streams,
                "working": working[a.id],
                "open_assigned": open_assigned.get(a.id, 0),
                "events_24h": events_24h.get(a.id, 0),
                "last_event_at": last_event_at.get(a.id),
                "throttled": (throttled or {}).get(a.id, 0),
                "wake": a.wake or "push",
            }
        )
    if visible is not None:
        for row in out:
            # Presence, rate limits and wake policy describe work outside the shared scope.
            row.update(
                last_seen_at=None,
                last_seen_via=None,
                online=False,
                live_streams=0,
                throttled=0,
                wake="push",
            )
    out.sort(
        key=lambda r: max(
            [t for t in (r["last_seen_at"], r["last_event_at"]) if t]
            or [datetime.min.replace(tzinfo=now.tzinfo)]
        ),
        reverse=True,
    )
    return out


# --- The plan: what each agent is about to run ------------------------------------------------

PLAN_HORIZON = timedelta(hours=24)
REASONS = ("due", "run_requested", "ask")


def plan(
    session: Session, *, now: datetime | None = None, until: datetime | None = None
) -> list[dict[str, Any]]:
    """What each agent will run between `now` and `until` (default a day ahead), soonest first.

    Per agent: `next`, the tasks the work loop would hand it by `until`, read with the real
    eligibility rule (`work.eligible` evaluated at `until`: the due gate with the 06:00 run time
    of date-only due dates, `run_requested_at`, open Asks, waiting-on, blocked pauses...), each
    with `reason` `due` (its run time, `dates.run_at`; `at` null when it has no due date and is
    takeable now), `run_requested` (someone pressed Run now; `at` = when) or `ask` (an open Ask
    the loop has not handed over yet; `at` = when it was asked); and `leases`, the tasks it
    holds now with their age. Agents with nothing coming last. A restricted caller sees only
    the agents and the lists it reaches (the read filter and `work.eligible`'s scope rule)."""
    from slate.service import work

    now = now or utcnow()
    until = until or now + PLAN_HORIZON
    q = select(Account).where(
        Account.disabled_at.is_(None),
        Account.kind == AccountKind.agent,
        Account.handle != SYSTEM_HANDLE,
    )
    visible = access.participant_ids(session)
    if visible is not None:
        q = q.where(Account.id.in_(visible))
    out = []
    for agent in session.scalars(q.order_by(Account.handle)):
        asked: dict[str, datetime] = {}
        for ask in work.pending_asks(session, agent.id):
            asked.setdefault(ask.item_id, ask.created_at)
        rows = list(session.scalars(work.eligible(session, agent.id, now=until)))
        lists = dict(
            session.execute(
                select(List.id, List.name).where(List.id.in_({i.list_id for i in rows}))
            ).all()
        )
        nxt = []
        for item in rows:
            pressed = item.run_requested_at is not None and (
                item.agent_state_at is None or item.run_requested_at > item.agent_state_at
            )
            if pressed:
                reason, at = "run_requested", item.run_requested_at
            elif item.id in asked:
                reason, at = "ask", asked[item.id]
            else:
                reason = "due"
                at = dates.run_at(item.due_at, item.due_date_only) if item.due_at else None
                paused = (
                    item.agent_state == AgentState.blocked
                    and item.agent_state_by == agent.id
                    and item.agent_state_at is not None
                )
                for later in (
                    item.start_at,
                    item.agent_state_at + work.BLOCKED_PAUSE if paused else None,
                ):
                    if later is not None and later > now and (at is None or later > at):
                        at = later
            nxt.append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "list": lists.get(item.list_id),
                    "due_at": item.due_at,
                    "reason": reason,
                    "at": at,
                }
            )
        nxt.sort(key=lambda n: (n["at"] is not None, n["at"] or now))
        leases = []
        for lease in work.held_by(session, agent.id):
            item = session.get(Item, lease["item_id"])
            leases.append(
                {
                    "item_id": lease["item_id"],
                    "title": item.title if item else None,
                    "claimed_at": lease["claimed_at"],
                    "until": lease["until"],
                    "age_seconds": max(0, int((now - lease["claimed_at"]).total_seconds())),
                }
            )
        out.append(
            {
                "id": agent.id,
                "handle": agent.handle,
                "display_name": agent.display_name,
                "next": nxt,
                "leases": leases,
            }
        )

    def soonest(entry: dict[str, Any]) -> tuple[int, bool, datetime, str]:
        if not entry["next"]:
            return (1, True, now, entry["handle"])
        first = entry["next"][0]["at"]
        return (0, first is not None, first or now, entry["handle"])

    out.sort(key=soonest)
    return out
