"""Scheduler hooks for due events, reminders and daily digests."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from slate.service import due, reminders


def due_events_tick(session: Session, now: datetime) -> int:
    """Scheduler hook: emit item.due_soon / item.due / item.overdue that have come due, the
    retries of failed runs whose time has come, and the agent cards left idle: stale working
    badges cleared, stalled cards poked and parked cards returned."""
    return (
        due.emit_due_events(session, now=now)
        + due.retry_failed_runs(session, now=now)
        + due.clear_stale_badges(session, now=now)
        + due.poke_stalled(session, now=now)
        + due.unpark_due(session, now=now)
    )


def reminders_tick(session: Session, now: datetime) -> int:
    """Scheduler hook: record `item.reminder` for reminders that have come due."""
    return reminders.fire_reminders(session, now=now)


def digest_tick(session: Session, now: datetime) -> int:
    """Scheduler hook: each account's daily digest at its hour."""
    return reminders.send_digests(session, now=now)
