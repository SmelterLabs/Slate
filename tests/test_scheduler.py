"""The in-process scheduler loop: ticks, hooks, start/stop, lifespan."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from slate import scheduler
from slate.app import create_app
from slate.models import Item, Source
from slate.service import accounts, lists, recurrences, sections
from slate.service.core import Actor
from slate.settings import settings


def _rule(db):
    account, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=account.id, source=Source.api)
    section = sections.create_section(db, actor, name="Home", audience="personal")
    chores = lists.create_list(db, actor, section_id=section.id, name="Chores")
    return recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="Daily",
        rrule="FREQ=DAILY",
        dtstart="2030-01-07",
    )


# Noon in Chicago on the rule's first day, far from a DST change: "now + 14 days" holds
# 15 midnights. (With the real clock, the hour after midnight in the two weeks before DST
# ends loses the last one.)
NOON = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)


def _count(db) -> int:
    return db.scalar(select(func.count()).select_from(Item))


def test_materialize_is_the_first_hook():
    assert scheduler.hooks[0] is recurrences.materialize


def test_tick_runs_materialize_and_is_idempotent(db, monkeypatch):
    _rule(db)
    before = scheduler.last_tick_at
    at = scheduler.tick(NOON)
    assert scheduler.last_tick_at == at and at != before
    first = _count(db)
    assert first == 15  # today + 14 days ahead
    scheduler.tick(NOON)  # a restart, or a second tick: no duplicates
    assert _count(db) == first


def test_a_failing_hook_does_not_stop_the_others(monkeypatch):
    seen = []

    def bad(session, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "hooks", [bad, lambda session, now: seen.append(now)])
    now = datetime(2026, 11, 1, tzinfo=UTC)
    scheduler.tick(now)
    assert seen == [now]
    assert scheduler.last_tick_at == now


def test_each_hook_gets_a_fresh_session_with_no_open_transaction(monkeypatch):
    sessions = []
    monkeypatch.setattr(scheduler, "hooks", [lambda s, now: sessions.append(s)] * 2)
    scheduler.tick()
    assert len({id(s) for s in sessions}) == 2
    assert not any(s.in_transaction() for s in sessions)


async def test_loop_ticks_until_stopped(monkeypatch):
    ticks = []
    monkeypatch.setattr(scheduler, "hooks", [lambda s, now: ticks.append(now)])
    task = scheduler.start(interval=0.01)
    try:
        for _ in range(200):
            if len(ticks) >= 3:
                break
            await asyncio.sleep(0.01)
    finally:
        await scheduler.stop()
    assert len(ticks) >= 3
    assert task.done()
    count = len(ticks)
    await asyncio.sleep(0.05)
    assert len(ticks) == count


def test_lifespan_starts_and_stops_the_scheduler_when_enabled(monkeypatch):
    ticks = []
    monkeypatch.setattr(scheduler, "hooks", [lambda s, now: ticks.append(now)])
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    with TestClient(create_app()):
        assert scheduler.running()
    assert not scheduler.running()


def test_lifespan_leaves_the_scheduler_off_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    with TestClient(create_app()):
        assert not scheduler.running()
