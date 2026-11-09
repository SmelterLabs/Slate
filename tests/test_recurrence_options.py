"""ClickUp's recurrence options: recur when done, skip weekends, reopen, copy checklists, and
the upcoming dates the calendar draws."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from slate.models import Event, Item, Source, Status, utcnow
from slate.service import accounts, checklists, items, lists, recurrences, sections
from slate.service.core import Actor
from slate.service.errors import Invalid

CT = ZoneInfo("America/Chicago")


def ct(*args) -> datetime:
    return datetime(*args, tzinfo=CT).astimezone(UTC)


@pytest.fixture
def actor(db) -> Actor:
    account, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    return Actor(account_id=account.id, source=Source.api)


@pytest.fixture
def chores(db, actor):
    section = sections.create_section(db, actor, name="Home", audience="personal")
    return lists.create_list(db, actor, section_id=section.id, name="Chores")


def _instances(db, rule) -> list[Item]:
    return list(
        db.scalars(select(Item).where(Item.recurrence_id == rule.id).order_by(Item.occurrence_at))
    )


def _local_day(dt: datetime) -> date:
    return dt.astimezone(CT).date()


def _today() -> date:
    return utcnow().astimezone(CT).date()


# --- skip weekends ---------------------------------------------------------------------------


def test_skip_weekends_moves_saturday_to_monday(db, actor, chores):
    rule = recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="Water plants",
        rrule="FREQ=WEEKLY;BYDAY=SA",
        dtstart="2026-11-01",
        skip_weekends=True,
        now=ct(2026, 11, 1),
    )
    days = [o.local.date() for o in recurrences.next_occurrences(rule, ct(2026, 11, 1), 3)]
    assert days == [date(2026, 11, 9), date(2026, 11, 16), date(2026, 11, 23)]
    assert all(d.weekday() == 0 for d in days)


def test_skip_weekends_daily_counts_monday_once(db, actor, chores):
    rule = recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="Standup",
        rrule="FREQ=DAILY",
        dtstart="2026-11-06",  # a Friday
        skip_weekends=True,
        now=ct(2026, 11, 6),
    )
    days = [o.local.date() for o in recurrences.next_occurrences(rule, ct(2026, 11, 6), 4)]
    assert days == [date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)]


def test_skip_weekends_spawns_on_monday(db, actor, chores):
    now = ct(2026, 11, 1, 9)
    rule = recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="Weekend chore {date}",
        rrule="FREQ=WEEKLY;BYDAY=SU",
        dtstart="2026-11-01",
        skip_weekends=True,
        lead_days=8,
        now=now,
    )
    recurrences.materialize(db, now=now)
    got = _instances(db, rule)
    assert [_local_day(i.due_at) for i in got] == [date(2026, 11, 2), date(2026, 11, 9)]
    assert got[0].title == "Weekend chore Mon Nov 2"


def test_preview_carries_options(db):
    out = recurrences.preview(
        "FREQ=WEEKLY;BYDAY=SA", dtstart="2026-11-01", skip_weekends=True, now=ct(2026, 11, 1)
    )
    assert out["occurrences"][0]["local"].startswith("2026-11-09")
    assert "weekends moved to Monday" in out["description"]


# --- recur when done -------------------------------------------------------------------------


def _on_done_rule(db, actor, chores, **fields):
    fields.setdefault("rrule", "FREQ=DAILY;INTERVAL=3")
    return recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="Change filter",
        trigger="on_done",
        **fields,
    )


def test_on_done_rule_makes_only_the_first_instance(db, actor, chores):
    rule = _on_done_rule(db, actor, chores)
    recurrences.materialize(db)
    recurrences.materialize(db)
    got = _instances(db, rule)
    assert len(got) == 1
    assert rule.next_run_at is None
    assert recurrences.recurrence_view(db, rule)["next_occurrences"] == []


def test_done_makes_the_next_one_from_the_day_it_was_done(db, actor, chores):
    rule = _on_done_rule(db, actor, chores, dtstart="2026-01-01")
    recurrences.materialize(db)
    (first,) = _instances(db, rule)
    items.update_item(db, actor, first.id, {"status": "done"})
    got = _instances(db, rule)
    assert len(got) == 2
    nxt = got[-1]
    assert nxt.id != first.id
    assert _local_day(nxt.due_at) == _today() + timedelta(days=3)
    assert nxt.title == "Change filter"
    types = [e.type for e in db.scalars(select(Event).where(Event.item_id == nxt.id))]
    assert "recurrence.spawned" in types


def test_only_the_newest_instance_makes_the_next(db, actor, chores):
    rule = _on_done_rule(db, actor, chores)
    recurrences.materialize(db)
    (first,) = _instances(db, rule)
    items.update_item(db, actor, first.id, {"status": "done"})
    items.update_item(db, actor, first.id, {"status": "to do"})
    items.update_item(db, actor, first.id, {"status": "done"})
    assert len(_instances(db, rule)) == 2


def test_paused_on_done_rule_makes_nothing(db, actor, chores):
    rule = _on_done_rule(db, actor, chores)
    recurrences.materialize(db)
    (first,) = _instances(db, rule)
    recurrences.pause_recurrence(db, actor, rule.id)
    items.update_item(db, actor, first.id, {"status": "done"})
    assert len(_instances(db, rule)) == 1


def test_schedule_rule_ignores_done(db, actor, chores):
    now = ct(2026, 11, 1)
    rule = recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="Trash",
        rrule="FREQ=WEEKLY;BYDAY=WE",
        dtstart="2026-11-01",
        lead_days=4,
        now=now,
    )
    recurrences.materialize(db, now=now)
    (first,) = _instances(db, rule)
    items.update_item(db, actor, first.id, {"status": "done"})
    assert len(_instances(db, rule)) == 1


def test_done_past_until_ends_the_rule(db, actor, chores):
    until = (_today() + timedelta(days=1)).strftime("%Y%m%d")
    rule = _on_done_rule(db, actor, chores, rrule=f"FREQ=DAILY;INTERVAL=3;UNTIL={until}")
    recurrences.materialize(db)
    (first,) = _instances(db, rule)
    items.update_item(db, actor, first.id, {"status": "done"})
    assert len(_instances(db, rule)) == 1
    assert rule.ended_at is not None


def test_reopen_reuses_the_task(db, actor, chores):
    rule = _on_done_rule(db, actor, chores, rrule="FREQ=WEEKLY", reopen=True)
    recurrences.materialize(db)
    (task,) = _instances(db, rule)
    cl = checklists.create_checklist(db, actor, task.id, name="Steps", items=["Buy", "Swap"])
    entry = checklists.checklists_of(db, task.id)[0]["items"][0]
    checklists.update_entry(db, actor, entry["id"], {"done": True})
    old_due = task.due_at
    items.update_item(db, actor, task.id, {"status": "done"})
    db.refresh(task)
    assert _instances(db, rule) == [task]
    assert task.completed_at is None
    assert db.get(Status, task.status_id).kind.value == "open"
    assert _local_day(task.due_at) == _today() + timedelta(days=7)
    assert task.due_at != old_due
    assert all(not e["done"] for e in checklists.checklists_of(db, task.id)[0]["items"])
    assert cl.id == checklists.checklists_of(db, task.id)[0]["id"]
    types = [e.type for e in db.scalars(select(Event).where(Event.item_id == task.id))]
    assert "recurrence.reopened" in types
    assert "checklist.entry_unchecked" in types


def test_reopen_needs_on_done(db, actor, chores):
    with pytest.raises(Invalid, match="on_done"):
        recurrences.create_recurrence(
            db,
            actor,
            list_id=chores.id,
            title_template="X",
            rrule="FREQ=DAILY",
            reopen=True,
        )
    rule = _on_done_rule(db, actor, chores, reopen=True)
    with pytest.raises(Invalid, match="on_done"):
        recurrences.update_recurrence(db, actor, rule.id, {"trigger": "schedule"})
    recurrences.update_recurrence(db, actor, rule.id, {"trigger": "schedule", "reopen": False})


def test_adopted_item_on_done(db, actor, chores):
    task = items.create_item(db, actor, list_id=chores.id, title="Water filter")
    rule = _on_done_rule(db, actor, chores, rrule="FREQ=MONTHLY", adopt_item_id=task.id)
    assert rule.next_run_at is None
    recurrences.materialize(db)
    assert _instances(db, rule) == [task]
    items.update_item(db, actor, task.id, {"status": "done"})
    got = _instances(db, rule)
    assert len(got) == 2
    assert got[-1].due_at > task.due_at


def test_preview_on_done_chains_from_today(db):
    now = ct(2026, 11, 4, 10)
    out = recurrences.preview("FREQ=DAILY;INTERVAL=3", trigger="on_done", count=3, now=now)
    days = [o["local"][:10] for o in out["occurrences"]]
    assert days == ["2026-11-07", "2026-11-10", "2026-11-13"]
    assert "counted from when each is done" in out["description"]


def test_next_after_done_weekly_on_monday():
    rule = recurrences.Recurrence(
        rrule="FREQ=WEEKLY;BYDAY=MO",
        timezone="America/Chicago",
        dtstart=ct(2026, 1, 5),
    )
    occ = recurrences.next_after_done(rule, ct(2026, 11, 4, 15))  # a Wednesday
    assert occ.local.date() == date(2026, 11, 9)
    occ = recurrences.next_after_done(rule, ct(2026, 11, 9, 8))  # done on the Monday itself
    assert occ.local.date() == date(2026, 11, 16)


@pytest.mark.parametrize("hour", [0, 9, 23])
def test_next_after_done_on_a_weekend_day_waits_a_whole_interval(hour):
    """Monthly on the 24th, skip weekends, done on Saturday Oct 24: next month (Tue Nov 24),
    not the Monday two days later (the done day's own occurrence moved to Monday)."""
    rule = recurrences.Recurrence(
        rrule="FREQ=MONTHLY;BYMONTHDAY=24",
        timezone="America/Chicago",
        dtstart=ct(2026, 10, 24),
        skip_weekends=True,
    )
    occ = recurrences.next_after_done(rule, ct(2026, 10, 24, hour, 25))
    assert occ.local.date() == date(2026, 11, 24)
    # And a next date that falls on a weekend still moves to Monday: Jan 24, 2027 is a Sunday.
    occ = recurrences.next_after_done(rule, ct(2026, 12, 24, 10))
    assert occ.local.date() == date(2027, 1, 25)


def test_next_after_done_on_the_sunday_after_a_saturday_date():
    """Done a day late, on the Sunday: the Saturday the 24th (moved to Monday the 26th) is still
    the one just finished, so the next is Tuesday Nov 24."""
    rule = recurrences.Recurrence(
        rrule="FREQ=MONTHLY;BYMONTHDAY=24",
        timezone="America/Chicago",
        dtstart=ct(2026, 10, 24),
        skip_weekends=True,
    )
    occ = recurrences.next_after_done(rule, ct(2026, 10, 25, 9))
    assert occ.local.date() == date(2026, 11, 24)


# --- copy checklists -------------------------------------------------------------------------


def test_new_instance_copies_checklists_unticked(db, actor, chores):
    rule = _on_done_rule(db, actor, chores)
    recurrences.materialize(db)
    (first,) = _instances(db, rule)
    checklists.create_checklist(db, actor, first.id, name="Steps", items=["Open", "Swap"])
    entry = checklists.checklists_of(db, first.id)[0]["items"][0]
    checklists.update_entry(db, actor, entry["id"], {"done": True})
    items.update_item(db, actor, first.id, {"status": "done"})
    nxt = _instances(db, rule)[-1]
    (copied,) = checklists.checklists_of(db, nxt.id)
    assert copied["name"] == "Steps"
    assert [e["title"] for e in copied["items"]] == ["Open", "Swap"]
    assert not any(e["done"] for e in copied["items"])


def test_copy_checklists_off(db, actor, chores):
    rule = _on_done_rule(db, actor, chores, copy_checklists=False)
    recurrences.materialize(db)
    (first,) = _instances(db, rule)
    checklists.create_checklist(db, actor, first.id, name="Steps", items=["Open"])
    items.update_item(db, actor, first.id, {"status": "done"})
    assert checklists.checklists_of(db, _instances(db, rule)[-1].id) == []


def test_scheduled_spawn_copies_previous_checklists(db, actor, chores):
    now = ct(2026, 11, 1)
    rule = recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="Trash {date}",
        rrule="FREQ=WEEKLY;BYDAY=WE",
        dtstart="2026-11-01",
        lead_days=4,
        now=now,
    )
    recurrences.materialize(db, now=now)
    (first,) = _instances(db, rule)
    checklists.create_checklist(db, actor, first.id, name="Bins", items=["Green", "Blue"])
    later = now + timedelta(days=7)
    recurrences.materialize(db, now=later)
    second = _instances(db, rule)[-1]
    assert second.id != first.id
    assert [e["title"] for e in checklists.checklists_of(db, second.id)[0]["items"]] == [
        "Green",
        "Blue",
    ]


# --- upcoming --------------------------------------------------------------------------------


def test_upcoming_lists_dates_not_yet_items(db, actor, chores):
    now = ct(2026, 11, 1, 9)
    rule = recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="Trash {date}",
        rrule="FREQ=WEEKLY;BYDAY=WE",
        dtstart="2026-11-01",
        lead_days=7,
        now=now,
    )
    recurrences.materialize(db, now=now)
    assert [_local_day(i.due_at) for i in _instances(db, rule)] == [date(2026, 11, 4)]
    got = recurrences.upcoming(db, start=date(2026, 11, 1), end=date(2026, 11, 30))
    assert [u["local_date"] for u in got] == ["2026-11-11", "2026-11-18", "2026-11-25"]
    assert got[0]["title"] == "Trash Wed Nov 11"
    assert got[0]["recurrence_id"] == rule.id
    recurrences.pause_recurrence(db, actor, rule.id)
    assert recurrences.upcoming(db, start=date(2026, 11, 1), end=date(2026, 11, 30)) == []


def test_upcoming_refuses_long_windows(db):
    with pytest.raises(Invalid):
        recurrences.upcoming(db, start=date(2026, 1, 1), end=date(2028, 1, 1))
    with pytest.raises(Invalid):
        recurrences.upcoming(db, start=date(2026, 2, 1), end=date(2026, 1, 1))


def test_upcoming_skips_on_done_rules(db, actor, chores):
    _on_done_rule(db, actor, chores)
    today = _today()
    assert recurrences.upcoming(db, start=today, end=today + timedelta(days=30)) == []


def test_view_has_options(db, actor, chores):
    rule = _on_done_rule(db, actor, chores, skip_weekends=True)
    view = recurrences.recurrence_view(db, rule)
    assert view["trigger"] == "on_done"
    assert view["skip_weekends"] is True
    assert view["reopen"] is False
    assert view["copy_checklists"] is True
