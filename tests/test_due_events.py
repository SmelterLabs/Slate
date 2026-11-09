"""item.due_soon / item.due / item.overdue: each once, at the right time, idempotent on restart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from slate import notify, scheduler
from slate.dates import LOCAL
from slate.models import Account, AccountKind, Event, Item, Source, utcnow
from slate.service import (
    accounts,
    chatwake,
    docwake,
    due,
    items,
    lists,
    recurrences,
    sections,
    sessions,
    trash,
    webhooks,
    work,
)
from slate.service.core import Actor, apply_change
from slate.service.errors import Invalid

DUE = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)  # 5 pm Chicago, Nov 2


@pytest.fixture
def world(db):
    alex, _, tok = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=alex.id, source=Source.api)
    section = sections.create_section(db, actor, name="Personal", audience="personal")
    todo = lists.create_list(db, actor, section_id=section.id, name="Todo")
    return {"actor": actor, "by": accounts.resolve_token(db, tok), "todo": todo}


def _item(db, world, due_at=DUE, **kw) -> Item:
    return items.create_item(
        db,
        world["actor"],
        list_id=world["todo"].id,
        title="Pay rent",
        due_at=due_at.isoformat(),
        **kw,
    )


def _due_events(db, item_id=None) -> list[Event]:
    q = select(Event).where(Event.type.in_(["item.due_soon", "item.due", "item.overdue"]))
    if item_id:
        q = q.where(Event.item_id == item_id)
    return list(db.scalars(q.order_by(Event.id)))


def _walk(db, start: datetime, end: datetime, step: timedelta) -> None:
    now = start
    while now <= end:
        due.emit_due_events(db, now=now)
        now += step


def test_each_stage_once_with_a_clock_crossing_the_boundaries(db, world):
    item = _item(db, world)
    _walk(db, DUE - timedelta(hours=30), DUE + timedelta(hours=5), timedelta(minutes=10))
    evs = _due_events(db, item.id)
    assert [e.type for e in evs] == ["item.due_soon", "item.due", "item.overdue"]
    assert evs[0].after_json == {"due_at": "2026-11-02T22:00:00.000000Z", "lead_hours": 24}
    assert all(e.field == "due_at" and e.source == Source.system for e in evs)
    system = db.scalar(select(Account).where(Account.handle == "slate"))
    assert system.kind == AccountKind.agent
    assert {e.actor_id for e in evs} == {system.id}
    db.expire_all()
    assert db.get(Item, item.id).last_due_event == "overdue"


def test_exact_boundaries(db, world):
    item = _item(db, world)
    for now, expected in [
        (DUE - timedelta(hours=24, seconds=1), []),
        (DUE - timedelta(hours=24), ["item.due_soon"]),
        (DUE - timedelta(seconds=1), ["item.due_soon"]),
        (DUE, ["item.due_soon", "item.due"]),
        (DUE + timedelta(minutes=59), ["item.due_soon", "item.due"]),
        (DUE + timedelta(hours=1), ["item.due_soon", "item.due", "item.overdue"]),
    ]:
        due.emit_due_events(db, now=now)
        assert [e.type for e in _due_events(db, item.id)] == expected, now


def test_restart_emits_no_duplicates(db, world, tmp_path):
    item = _item(db, world)
    due.emit_due_events(db, now=DUE + timedelta(minutes=5))
    db.close()
    # A "new process": fresh session, same database, same and later ticks.
    from slate.db import SessionLocal

    with SessionLocal() as fresh:
        assert due.emit_due_events(fresh, now=DUE + timedelta(minutes=5)) == 0
        assert due.emit_due_events(fresh, now=DUE + timedelta(minutes=30)) == 0
        assert due.emit_due_events(fresh, now=DUE + timedelta(hours=2)) == 1
        assert due.emit_due_events(fresh, now=DUE + timedelta(days=3)) == 0
        assert [e.type for e in _due_events(fresh, item.id)] == ["item.due", "item.overdue"]


def test_late_start_skips_due_soon_but_not_due(db, world):
    item = _item(db, world)
    due.emit_due_events(db, now=DUE + timedelta(hours=3))  # the server was down all day
    assert [e.type for e in _due_events(db, item.id)] == ["item.due", "item.overdue"]


def test_done_archived_and_undated_items_emit_nothing(db, world):
    done = _item(db, world, status="done")
    archived = _item(db, world)
    items.archive_item(db, world["actor"], archived.id)
    control = _item(db, world)
    items.create_item(db, world["actor"], list_id=world["todo"].id, title="no date")
    archived_list = lists.create_list(
        db, world["actor"], section_id=world["todo"].section_id, name="Old"
    )
    in_archived_list = items.create_item(
        db, world["actor"], list_id=archived_list.id, title="x", due_at=DUE.isoformat()
    )
    lists.archive_list(db, world["actor"], archived_list.id)
    _walk(db, DUE - timedelta(days=2), DUE + timedelta(hours=2), timedelta(hours=1))
    emitted = {e.item_id for e in _due_events(db)}
    assert done.id not in emitted and archived.id not in emitted
    assert in_archived_list.id not in emitted
    assert emitted == {control.id}


def test_completing_before_overdue_stops_overdue(db, world):
    item = _item(db, world)
    due.emit_due_events(db, now=DUE)
    items.update_item(db, world["actor"], item.id, {"status": "done"})
    due.emit_due_events(db, now=DUE + timedelta(hours=2))
    assert [e.type for e in _due_events(db, item.id)] == ["item.due"]


def test_one_due_soon_per_distinct_lead(db, world):
    actor = world["actor"]
    webhooks.create_webhook(db, actor, url="https://a.example.com", events=["item.due_soon"])
    webhooks.create_webhook(
        db, actor, url="https://b.example.com", events=["*"], filter={"due_lead_hours": 2}
    )
    webhooks.create_webhook(
        db, actor, url="https://c.example.com", events=["item.due"], filter={"due_lead_hours": 48}
    )  # does not want due_soon, so 48 is not a lead
    item = _item(db, world)
    _walk(db, DUE - timedelta(hours=50), DUE, timedelta(minutes=30))
    soon = [e.after_json.get("lead_hours") for e in _due_events(db, item.id)]
    assert soon[:2] == [24, 2]
    assert [e.type for e in _due_events(db, item.id)][2:] == ["item.due"]


def test_item_created_inside_the_window_gets_every_lead_once(db, world):
    actor = world["actor"]
    webhooks.create_webhook(db, actor, url="https://a.example.com", events=["item.due_soon"])
    webhooks.create_webhook(
        db,
        actor,
        url="https://b.example.com",
        events=["item.due_soon"],
        filter={"due_lead_hours": 2},
    )
    item = _item(db, world)
    due.emit_due_events(db, now=DUE - timedelta(hours=1))
    due.emit_due_events(db, now=DUE - timedelta(minutes=30))
    assert [e.after_json["lead_hours"] for e in _due_events(db, item.id)] == [24, 2]


def test_moving_the_due_date_later_starts_over(db, world):
    item = _item(db, world)
    due.emit_due_events(db, now=DUE + timedelta(hours=2))  # due + overdue
    later = DUE + timedelta(days=7)
    items.update_item(db, world["actor"], item.id, {"due_at": later.isoformat()})
    due.emit_due_events(db, now=DUE + timedelta(hours=3))
    assert len(_due_events(db, item.id)) == 2
    _walk(db, later - timedelta(hours=25), later + timedelta(hours=2), timedelta(hours=1))
    assert [e.type for e in _due_events(db, item.id)] == [
        "item.due",
        "item.overdue",
        "item.due_soon",
        "item.due",
        "item.overdue",
    ]


def test_due_events_do_not_touch_updated_at(db, world):
    item = _item(db, world)
    before = item.updated_at
    due.emit_due_events(db, now=DUE)
    db.expire_all()
    assert db.get(Item, item.id).updated_at == before


def test_due_events_reach_webhook_subscribers(db, world, monkeypatch):
    import httpx

    seen = []
    monkeypatch.setattr(
        notify, "transport", httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(204))
    )
    webhooks.create_webhook(db, world["actor"], url="https://a.example.com", events=["item.due"])
    d = notify.Dispatcher()
    d.start()
    item = _item(db, world)
    due.emit_due_events(db, now=DUE)
    d.fan_out_once()
    d.send_due_once()
    [req] = seen
    assert req.headers["X-Slate-Event"] == "item.due"
    body = req.read().decode()
    assert item.id in body and '"handle":"slate"' in body


def test_due_events_are_a_scheduler_hook_after_recurrence(db, world):
    # After materialize, so instances spawned this tick get their due events in the same tick.
    assert scheduler.hooks == [
        recurrences.materialize,
        notify.due_events_tick,
        notify.reminders_tick,
        notify.digest_tick,
        trash.purge_expired,
        work.expire_leases,
        chatwake.expire,
        docwake.expire,
        sessions.notify_expiring,
    ]
    item = _item(db, world)
    scheduler.tick(DUE)
    assert [e.type for e in _due_events(db, item.id)] == ["item.due"]
    scheduler.tick(DUE)  # a repeated tick emits nothing new
    assert [e.type for e in _due_events(db, item.id)] == ["item.due"]


# --- The run stage: an agent's task wakes its runner at its run time ------------------------


def _runs(db, item_id):
    return list(
        db.scalars(
            select(Event)
            .where(Event.type == "item.run_requested", Event.item_id == item_id)
            .order_by(Event.id)
        )
    )


def test_date_only_agent_task_gets_a_run_event_at_six_chicago(db, world):
    agent = accounts.create_account(
        db, world["actor"], handle="agent", display_name="Agent", kind="agent"
    )
    item = items.create_item(
        db,
        world["actor"],
        list_id=world["todo"].id,
        title="Morning report",
        due_at="2026-11-03",
        assignees=["agent"],
    )
    six = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # 06:00 CST
    due.emit_due_events(db, now=six - timedelta(minutes=1))
    assert _runs(db, item.id) == []
    due.emit_due_events(db, now=six)
    runs = _runs(db, item.id)
    assert len(runs) == 1
    assert runs[0].after_json["for"] == [agent.id]
    assert runs[0].after_json["reason"] == "due"
    assert runs[0].source == Source.system
    # Later ticks never repeat it; due and overdue still follow at 17:00 and 18:00.
    _walk(db, six, six + timedelta(hours=13), timedelta(minutes=30))
    assert len(_runs(db, item.id)) == 1
    assert [e.type for e in _due_events(db, item.id)][-2:] == ["item.due", "item.overdue"]


def test_timed_agent_task_runs_at_its_due_time_and_people_tasks_get_no_run(db, world):
    accounts.create_account(db, world["actor"], handle="agent", display_name="Agent", kind="agent")
    agent_task = _item(db, world, assignees=["agent"])
    person_task = _item(db, world)
    due.emit_due_events(db, now=DUE - timedelta(seconds=1))
    assert _runs(db, agent_task.id) == []
    due.emit_due_events(db, now=DUE)
    assert len(_runs(db, agent_task.id)) == 1
    assert [e.type for e in _due_events(db, agent_task.id)][-1] == "item.due"
    assert _runs(db, person_task.id) == []


def test_no_run_request_when_its_agent_already_took_it(db, world):
    """Assigned after the run time: the assignment woke the agent, so the scheduler's catch-up
    run request would only read as a second run on the card. (Real clock: `take` stamps the
    badge with the time now.)"""
    agent = accounts.create_account(
        db, world["actor"], handle="agent", display_name="Agent", kind="agent"
    )
    yesterday = (utcnow().astimezone(LOCAL) - timedelta(days=1)).date().isoformat()
    item = items.create_item(
        db, world["actor"], list_id=world["todo"].id, title="Report", due_at=yesterday
    )
    items.update_item(db, world["actor"], item.id, {"assignees": ["agent"]})
    agent_actor = Actor(account_id=agent.id, source=Source.api)
    lease = work.take(db, agent_actor, item.id)
    due.emit_due_events(db, now=utcnow() + timedelta(seconds=30))
    assert _runs(db, item.id) == []
    assert [e.type for e in _due_events(db, item.id)] == ["item.due", "item.overdue"]
    # The run never comes back once the agent is done.
    work.finish(db, agent_actor, item.id, outcome="review", run_id=lease.run_id)
    due.emit_due_events(db, now=utcnow() + timedelta(minutes=5))
    assert _runs(db, item.id) == []


def test_a_take_before_the_run_time_does_not_cancel_the_run(db, world):
    agent = accounts.create_account(
        db, world["actor"], handle="agent", display_name="Agent", kind="agent"
    )
    # Due tomorrow, so the take and finish (real clock: finish checks the lease against it)
    # come before the 6am run time.
    tomorrow = (utcnow().astimezone(LOCAL) + timedelta(days=1)).date()
    item = items.create_item(
        db,
        world["actor"],
        list_id=world["todo"].id,
        title="Report",
        due_at=tomorrow.isoformat(),
        assignees=["agent"],
    )
    six = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 6, tzinfo=LOCAL).astimezone(UTC)
    agent_actor = Actor(account_id=agent.id, source=Source.api)
    lease = work.take(db, agent_actor, item.id)
    work.finish(db, agent_actor, item.id, outcome="review", run_id=lease.run_id)
    due.emit_due_events(db, now=six)
    assert len(_runs(db, item.id)) == 1


def test_a_renamed_or_disabled_system_account_is_put_back(db, world):
    actor = due.system_actor(db)
    system = db.get(Account, actor.account_id)
    with apply_change(db, world["actor"]) as cx:  # what an admin could do before the guard
        cx.set(system, "display_name", "Slate (retired)", "account.updated")
        cx.set(system, "disabled_at", datetime(2026, 10, 29, tzinfo=UTC), "account.disabled")
    assert due.system_actor(db).account_id == system.id
    db.refresh(system)
    assert system.display_name == "Slate" and system.disabled_at is None
    fixes = db.scalars(
        select(Event).where(Event.subject_id == system.id, Event.actor_id == system.id)
    ).all()
    assert {e.type for e in fixes} >= {"account.updated", "account.enabled"}


def test_the_system_account_cannot_be_renamed_or_disabled(db, world):
    system = db.get(Account, due.system_actor(db).account_id)
    for change in ({"display_name": "Renamed"}, {"disabled": True}, {"wake": "manual"}):
        with pytest.raises(Invalid, match="Slate's own account"):
            accounts.update_account(
                db, world["actor"], account_id=system.id, by=world["by"], **change
            )
