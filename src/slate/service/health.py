"""The Health page: is Slate working, and what broke? Read-only, except the poller alerts.

`report()` puts together, for owners and admins (`GET /api/v1/health`, MCP `slate_health`):

- `checks`: one line per thing that can break, each `ok`, `warn`, `fail` or `off`, and the
  overall `status` (the worst of them; `off` never counts). The same lines without their
  details are `summary()`, served unauthenticated at `GET /health` for agents and uptime checks.
- `pollers`: each scheduler hook (slate.monitor.pollers): last run, last success, the error,
  failures in a row, and whether a run is stuck (`STUCK_AFTER`) or overdue.
- `engines`: the asyncio engines (scheduler, webhooks, automations, push, email): whether each
  runs, and for the event-log engines whether their steps fail (slate.monitor.engines).
- `agents`: calls per token over the last hour: errors, error rate, slow calls, average and
  worst time; `recent_errors` and `slow_calls` are examples.
- `latency`: request percentiles over the kept samples of every route (slate.fastlane), and
  the slowest routes by p95.
- `automations`: failed and partial runs in the last day (the run log).
- `queues`: webhook, push and email deliveries waiting, webhook deliveries failed, and how far
  each event-log engine is behind.
- `db`: the database and WAL size, free disk, row counts.

Counters live in memory (slate.monitor) and start over on restart; the run log, deliveries and
engine cursors are read from the database. No new tables.

Alerts: when a poller fails `settings.health_alert_after` runs in a row the scheduler calls
`record_change()` (for an engine whose steps fail, `slate.engine` does, check
`engine:<name>`), which records one `health.alert` event (actor `slate`, source `system`);
when it works again, one `health.recovered`. Owners and admins get both in their inbox
(service/inbox.py) and as a push notification.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate import __version__, fastlane, monitor
from slate.models import (
    Account,
    Attachment,
    Automation,
    AutomationRun,
    Delivery,
    DocFile,
    EmailDelivery,
    EngineCursor,
    Event,
    Item,
    MessageFile,
    PushDelivery,
    Role,
    Token,
    utcnow,
)
from slate.service import access
from slate.service.core import apply_change
from slate.service.errors import Forbidden
from slate.settings import settings

# key -> (label, how often it should run). Order is the page's order.
POLLERS: dict[str, tuple[str, timedelta]] = {
    "reminders": ("Reminders", timedelta(minutes=1)),
    "digest": ("Daily digest", timedelta(minutes=1)),
    "due": ("Due and overdue nudges", timedelta(minutes=1)),
    "recurring": ("Recurring tasks", timedelta(minutes=1)),
    "trash": ("Trash purge", timedelta(minutes=1)),
    "leases": ("Agent task leases", timedelta(minutes=1)),
    "chat_turns": ("Agent chat turns", timedelta(minutes=1)),
    "doc_turns": ("Agent doc turns", timedelta(minutes=1)),
    "sessions": ("Guest session expiry notices", timedelta(minutes=1)),
    "chat_uploads": ("Unsent chat upload sweep", timedelta(minutes=1)),
}
ENGINES: dict[str, str] = {
    "scheduler": "Scheduler",
    "webhooks": "Webhook delivery",
    "automations": "Automations engine",
    "push": "Push notifications",
    "email": "Email notifications",
}
# Event-log engines and their cursor rows (engine_cursors).
CURSORS: dict[str, str] = {
    "automations": "automations",
    "push": "push",
    "email": "email",
}
STUCK_AFTER = timedelta(minutes=5)
LAG_CAP = 10_000  # count at most this many events behind
LAG_WARN = 1_000
WEBHOOKS_PENDING_WARN = 200
ERROR_RATE_WARN = 0.2  # with at least ERROR_RATE_MIN_CALLS calls in the hour
ERROR_RATE_MIN_CALLS = 10
DISK_WARN = 1024**3
DISK_FAIL = 200 * 1024**2
RANK = {"off": -1, "ok": 0, "warn": 1, "fail": 2}


def require_viewer(session: Session, account: Account) -> None:
    """Owners and admins, with a token that is not limited to some lists."""
    found = access.current(session)
    if Role(account.role or Role.member) not in (Role.owner, Role.admin) or (
        found is not None and found.limited
    ):
        raise Forbidden("only an owner or admin can see Slate's health")


def _check(key: str, label: str, status: str, detail: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail}


def _ago(then: datetime | None, now: datetime) -> str:
    if then is None:
        return "never"
    s = int((now - then).total_seconds())
    if s < 90:
        return f"{max(s, 0)} s ago"
    if s < 5400:
        return f"{s // 60} min ago"
    return f"{s // 3600} h ago"


# --- pollers and engines -----------------------------------------------------------------------


def _configured(session: Session, key: str) -> str | None:
    """Why a poller has nothing to do, or None when it is set up."""
    if not settings.scheduler_enabled:
        return "the scheduler is off (SLATE_SCHEDULER_ENABLED=false)"
    if key == "trash" and settings.trash_days <= 0:
        return "the trash is kept until emptied (SLATE_TRASH_DAYS=0)"
    return None


def _pollers(session: Session, now: datetime, uptime: float) -> list[dict[str, Any]]:
    states = monitor.pollers.snapshot()
    keys = list(POLLERS) + [k for k in states if k not in POLLERS]
    out = []
    for key in keys:
        label, every = POLLERS.get(key, (key, timedelta(minutes=1)))
        s = states.get(key) or monitor.Poller(key).view()
        off = _configured(session, key)
        overdue = max(every * 3, timedelta(minutes=5))
        running = s["running_since"]
        if running is not None and now - running > STUCK_AFTER:
            status, detail = "fail", f"Stuck: running for {_ago(running, now).replace(' ago', '')}"
        elif off and not s["runs"]:
            status, detail = "off", f"Off: {off}"
        elif s["consecutive_failures"] >= max(1, settings.health_alert_after):
            status = "fail"
            detail = f"Failed {s['consecutive_failures']} runs in a row: {s['last_error']}"
        elif s["consecutive_failures"]:
            status, detail = "warn", f"Last run failed: {s['last_error']}"
        elif (
            settings.scheduler_enabled
            and s["last_run_at"] is not None
            and now - s["last_run_at"] > overdue
        ):
            status, detail = "fail", f"No run since {_ago(s['last_run_at'], now)}"
        elif s["last_run_at"] is None:
            late = settings.scheduler_enabled and uptime > overdue.total_seconds()
            status = "fail" if late and not off else "ok"
            detail = "Has not run since the restart" if late else "Waiting for its first run"
        else:
            status, detail = "ok", f"Last worked {_ago(s['last_ok_at'], now)}"
        out.append(
            {
                "key": key,
                "label": label,
                "every_s": int(every.total_seconds()),
                "status": status,
                "detail": detail,
                "configured": off is None,
                **s,
            }
        )
    return out


def _engines() -> list[dict[str, Any]]:
    states = monitor.task_states()
    steps = monitor.engines.snapshot()
    out = []
    for key, label in ENGINES.items():
        st = states.get(key)
        failing = (steps.get(key) or {}).get("consecutive_failures", 0)
        error = (steps.get(key) or {}).get("last_error")
        if st is None:
            status, detail = "off", "Not started in this process"
        elif st["state"] == "running" and failing >= max(1, settings.health_alert_after):
            status, detail = "fail", f"Failed {failing} steps in a row: {error}"
        elif st["state"] == "running" and failing:
            status, detail = "warn", f"Last step failed: {error}"
        elif st["state"] == "running":
            status, detail = "ok", "Running"
        elif st["state"] == "off":
            why = st["detail"] or "turned off"
            # Webhooks refusing to start for want of a key is a misconfiguration, not a choice.
            status = "warn" if key == "webhooks" and "SECRET_KEY" in why else "off"
            detail = f"Off: {why}"
        else:
            status, detail = "fail", f"Stopped: {st['detail']}"
        out.append({"key": key, "label": label, "status": status, "detail": detail})
    return out


# --- agents --------------------------------------------------------------------------------


def _who(session: Session, account_ids: set[str], token_ids: set[str]) -> tuple[dict, dict]:
    accts = (
        {a.id: a for a in session.scalars(select(Account).where(Account.id.in_(account_ids)))}
        if account_ids
        else {}
    )
    toks = (
        {t.id: t for t in session.scalars(select(Token).where(Token.id.in_(token_ids)))}
        if token_ids
        else {}
    )
    return accts, toks


def _named(row: dict[str, Any], accts: dict, toks: dict) -> dict[str, Any]:
    a = accts.get(row["account_id"])
    t = toks.get(row["token_id"])
    return {
        **row,
        "at": datetime.fromtimestamp(row["at"], tz=UTC),
        "handle": a.handle if a else None,
        "display_name": a.display_name if a else None,
        "kind": a.kind.value if a else None,
        "token_label": t.label if t else None,
    }


def _agents(session: Session) -> dict[str, Any]:
    rows = monitor.calls.snapshot()
    errors = monitor.calls.recent("errors")[:20]
    slow = monitor.calls.recent("slow")[:20]
    every = rows + errors + slow
    accts, toks = _who(session, {r["account_id"] for r in every}, {r["token_id"] for r in every})
    tokens = []
    for r in rows:
        a, t = accts.get(r["account_id"]), toks.get(r["token_id"])
        last = r["last_error"]
        tokens.append(
            {
                **r,
                "last_at": datetime.fromtimestamp(r["last_at"], tz=UTC),
                "last_error": _named(last, accts, toks) if last else None,
                "handle": a.handle if a else None,
                "display_name": a.display_name if a else None,
                "kind": a.kind.value if a else None,
                "token_label": t.label if t else None,
            }
        )
    return {
        "tokens": tokens,
        "recent_errors": [_named(r, accts, toks) for r in errors],
        "slow_calls": [_named(r, accts, toks) for r in slow],
    }


# --- automations, queues, database --------------------------------------------------------------


def _automations(session: Session, now: datetime) -> dict[str, Any]:
    since = now - timedelta(days=1)
    counts = dict(
        session.execute(
            select(AutomationRun.status, func.count())
            .where(AutomationRun.at >= since, AutomationRun.status.in_(("failed", "partial")))
            .group_by(AutomationRun.status)
        ).all()
    )
    recent = session.execute(
        select(AutomationRun, Automation.name)
        .join(Automation, Automation.id == AutomationRun.automation_id)
        .where(AutomationRun.at >= since, AutomationRun.status.in_(("failed", "partial")))
        .order_by(AutomationRun.at.desc())
        .limit(10)
    ).all()
    item_ids = {r.item_id for r, _ in recent if r.item_id}
    titles = (
        dict(session.execute(select(Item.id, Item.title).where(Item.id.in_(item_ids))).all())
        if item_ids
        else {}
    )
    runs = []
    for run, name in recent:
        bad = [r for r in run.results_json or [] if isinstance(r, dict) and not r.get("ok")]
        runs.append(
            {
                "id": run.id,
                "automation_id": run.automation_id,
                "name": name,
                "item_id": run.item_id,
                "item_title": titles.get(run.item_id or ""),
                "status": run.status,
                "at": run.at,
                "detail": "; ".join(f"{r.get('action')}: {r.get('detail')}" for r in bad[:3])
                or None,
            }
        )
    return {
        "failed_24h": counts.get("failed", 0),
        "partial_24h": counts.get("partial", 0),
        "recent": runs,
    }


def _lag(session: Session, cursor_name: str) -> int | None:
    row = session.get(EngineCursor, cursor_name)
    if row is None:
        return None
    q = select(Event.id)
    if row.event_id:
        q = q.where(Event.id > row.event_id)
    return session.scalar(select(func.count()).select_from(q.limit(LAG_CAP).subquery())) or 0


def _queues(session: Session, now: datetime) -> dict[str, Any]:
    pending = (
        session.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(Delivery.state.in_(("pending", "sending")))
        )
        or 0
    )
    failed = (
        session.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(Delivery.state == "failed", Delivery.at >= now - timedelta(days=1))
        )
        or 0
    )
    waiting = {
        name: session.scalar(
            select(func.count()).select_from(model).where(model.state == "pending")
        )
        or 0
        for name, model in (("push_pending", PushDelivery), ("email_pending", EmailDelivery))
    }
    return {
        "webhooks_pending": pending,
        "webhooks_failed_24h": failed,
        **waiting,
        "engine_lag": {name: _lag(session, cur) for name, cur in CURSORS.items()},
    }


def _db(session: Session) -> dict[str, Any]:
    path = str(settings.database_path)
    size = wal = free = None
    if path != ":memory:":
        size = os.path.getsize(path) if os.path.exists(path) else None
        wal = os.path.getsize(f"{path}-wal") if os.path.exists(f"{path}-wal") else 0
        try:
            free = shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free
        except OSError:
            free = None
    return {
        "path": path,
        "bytes": size,
        "wal_bytes": wal,
        "disk_free_bytes": free,
        "events": session.scalar(select(func.count()).select_from(Event)) or 0,
        "items": session.scalar(select(func.count()).select_from(Item)) or 0,
        # Bytes held by task attachments, doc files (pages' images and file docs' revisions)
        # and chat files: what grows the one SQLite file as documents are uploaded.
        "blob_bytes": (session.scalar(select(func.coalesce(func.sum(Attachment.size), 0))) or 0)
        + (session.scalar(select(func.coalesce(func.sum(DocFile.size), 0))) or 0)
        + (session.scalar(select(func.coalesce(func.sum(MessageFile.size), 0))) or 0),
    }


# --- the report -------------------------------------------------------------------------------


def _scheduler(now: datetime) -> dict[str, Any]:
    from slate import scheduler

    last = scheduler.last_tick_at
    return {
        "enabled": settings.scheduler_enabled,
        "interval_s": settings.scheduler_interval_seconds,
        "last_tick_at": last,
        "last_tick_age_s": None if last is None else round((now - last).total_seconds(), 1),
    }


def _checks(r: dict[str, Any], uptime: float) -> list[dict[str, Any]]:
    out = []
    sch = r["scheduler"]
    if not sch["enabled"]:
        out.append(_check("scheduler", "Scheduler", "off", "Off (SLATE_SCHEDULER_ENABLED=false)"))
    else:
        limit = max(3 * sch["interval_s"], 300)
        age = sch["last_tick_age_s"]
        if age is None:
            late = uptime > limit
            out.append(
                _check(
                    "scheduler",
                    "Scheduler",
                    "fail" if late else "ok",
                    "No tick since the restart" if late else "Starting",
                )
            )
        else:
            out.append(
                _check(
                    "scheduler",
                    "Scheduler",
                    "fail" if age > limit else "ok",
                    f"Last tick {int(age)} s ago",
                )
            )
    for p in r["pollers"]:
        out.append(_check(f"poller:{p['key']}", p["label"], p["status"], p["detail"]))
    for e in r["engines"]:
        if e["key"] != "scheduler":  # the scheduler line above already covers it
            out.append(_check(f"engine:{e['key']}", e["label"], e["status"], e["detail"]))
    tokens = r["agents"]["tokens"]
    server = sum(t["server_errors"] for t in tokens)
    noisy = [
        t
        for t in tokens
        if t["calls"] >= ERROR_RATE_MIN_CALLS and t["error_rate"] >= ERROR_RATE_WARN
    ]
    if server:
        out.append(
            _check(
                "agents:errors", "Agent errors", "fail", f"{server} server errors in the last hour"
            )
        )
    elif noisy:
        names = ", ".join(t["handle"] or t["token_id"] for t in noisy[:3])
        out.append(
            _check(
                "agents:errors",
                "Agent errors",
                "warn",
                f"High error rate in the last hour: {names}",
            )
        )
    else:
        calls = sum(t["calls"] for t in tokens)
        out.append(_check("agents:errors", "Agent errors", "ok", f"{calls} calls in the last hour"))
    lat = r["latency"]
    p95 = lat.get("p95_ms")
    if p95 is not None and p95 >= settings.health_slow_ms:
        out.append(_check("latency", "Response time", "warn", f"p95 {p95:.0f} ms"))
    else:
        out.append(
            _check(
                "latency",
                "Response time",
                "ok",
                f"p95 {p95:.0f} ms" if p95 is not None else "No requests yet",
            )
        )
    au = r["automations"]
    if au["failed_24h"] or au["partial_24h"]:
        out.append(
            _check(
                "automations",
                "Automation runs",
                "warn",
                f"{au['failed_24h']} failed, {au['partial_24h']} partly failed in the last day",
            )
        )
    else:
        out.append(_check("automations", "Automation runs", "ok", "No failures in the last day"))
    q = r["queues"]
    behind = {k: v for k, v in q["engine_lag"].items() if v is not None and v >= LAG_WARN}
    bits = []
    if q["webhooks_pending"] >= WEBHOOKS_PENDING_WARN:
        bits.append(f"{q['webhooks_pending']} webhook deliveries waiting")
    for key, what in (("push_pending", "push notifications"), ("email_pending", "emails")):
        if q[key] >= WEBHOOKS_PENDING_WARN:
            bits.append(f"{q[key]} {what} waiting")
    if q["webhooks_failed_24h"]:
        bits.append(f"{q['webhooks_failed_24h']} webhook deliveries failed today")
    bits += [f"{ENGINES.get(k, k)} {v} events behind" for k, v in behind.items()]
    out.append(
        _check("queues", "Queues", "warn" if bits else "ok", "; ".join(bits) or "Nothing waiting")
    )
    free = r["db"]["disk_free_bytes"]
    if free is not None:
        gb = free / 1024**3
        status = "fail" if free < DISK_FAIL else "warn" if free < DISK_WARN else "ok"
        out.append(_check("disk", "Disk space", status, f"{gb:.1f} GB free"))
    return out


def worst(checks: list[dict[str, Any]]) -> str:
    rank = max((RANK[c["status"]] for c in checks), default=0)
    return {0: "ok", 1: "warn", 2: "fail"}.get(rank, "ok")


def report(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    uptime = time.time() - fastlane.stats.started
    r: dict[str, Any] = {
        "at": now,
        "version": __version__,
        "uptime_s": round(uptime, 1),
        "window_minutes": monitor.WINDOW_MIN,
        "slow_ms": settings.health_slow_ms,
        "alert_after": settings.health_alert_after,
        "scheduler": _scheduler(now),
        "pollers": _pollers(session, now, uptime),
        "engines": _engines(),
        "agents": _agents(session),
        "latency": {
            **fastlane.stats.overall(),
            "slowest": sorted(fastlane.latency()["routes"], key=lambda x: -x["p95_ms"])[:5],
        },
        "automations": _automations(session, now),
        "queues": _queues(session, now),
        "db": _db(session),
    }
    r["checks"] = _checks(r, uptime)
    r["status"] = worst(r["checks"])
    return r


def summary(session: Session) -> dict[str, Any]:
    """The checks without details, for anyone (`GET /health`)."""
    r = report(session)
    return {
        "status": r["status"],
        "version": r["version"],
        "at": r["at"],
        "checks": [{k: c[k] for k in ("key", "label", "status")} for c in r["checks"]],
    }


# --- alerts -----------------------------------------------------------------------------------


def record_change(
    session: Session,
    key: str,
    change: str,
    error: str | None,
    now: datetime,
    *,
    kind: str = "poller",
) -> None:
    """One `health.alert` (a poller, or an engine's steps with `kind="engine"`, failed
    `alert_after` times in a row) or `health.recovered`."""
    from slate.service.due import system_actor

    if kind == "engine":
        state = monitor.engines.snapshot().get(key, {})
        label = ENGINES.get(key, key)
    else:
        state = monitor.pollers.snapshot().get(key, {})
        label = POLLERS.get(key, (key, None))[0]
    after = {
        "check": f"{kind}:{key}",
        "label": label,
        "failures": state.get("consecutive_failures", 0),
        "error": (error or state.get("last_error") or None),
        "last_ok_at": state.get("last_ok_at").isoformat() if state.get("last_ok_at") else None,
    }
    kind = "health.alert" if change == "alert" else "health.recovered"
    with apply_change(session, system_actor(session)) as cx:
        cx.record(kind, after=after)
