"""Recurrence rules and materialization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from slate.models import Event, Item, Source
from slate.service import accounts, items, lists, recurrences, sections
from slate.service.core import Actor
from slate.service.errors import Invalid

CT = ZoneInfo("America/Chicago")


def ct(*args) -> datetime:
    """A Chicago wall time as aware UTC."""
    return datetime(*args, tzinfo=CT).astimezone(UTC)


@pytest.fixture
def actor(db) -> Actor:
    account, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    return Actor(account_id=account.id, source=Source.api)


@pytest.fixture
def chores(db, actor):
    section = sections.create_section(db, actor, name="Home", audience="personal")
    return lists.create_list(db, actor, section_id=section.id, name="Chores")


def _rule(db, actor, chores, now, **fields):
    fields.setdefault("title_template", "Bulk trash {date}")
    fields.setdefault("rrule", "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE")
    fields.setdefault("dtstart", "2026-11-11")
    return recurrences.create_recurrence(db, actor, list_id=chores.id, now=now, **fields)


def _instances(db, rule) -> list[Item]:
    return list(
        db.scalars(select(Item).where(Item.recurrence_id == rule.id).order_by(Item.occurrence_at))
    )


def _complete(db, actor, item):
    items.update_item(db, actor, item.id, {"status": "done"})


# --- validation ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rrule",
    [
        "",
        "INTERVAL=2",
        "FREQ=BOGUS",
        "FREQ=HOURLY",
        "FREQ=MINUTELY",
        "DTSTART:20260101\nFREQ=DAILY",
    ],
)
def test_bad_rrule_is_refused(db, actor, chores, rrule):
    with pytest.raises(Invalid):
        _rule(db, actor, chores, ct(2026, 11, 1), rrule=rrule)


def test_rrule_prefix_and_case_are_normalized(db, actor, chores):
    rule = _rule(db, actor, chores, ct(2026, 11, 1), rrule="RRULE:freq=weekly;byday=we")
    assert rule.rrule == "FREQ=WEEKLY;BYDAY=WE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timezone", "Mars/Olympus"),
        ("timezone", ""),
        ("lead_days", -1),
        ("lead_days", 400),
        ("missed_policy", "whenever"),
        ("priority", "huge"),
        ("title_template", "  "),
        ("dtstart", "next tuesday"),
        ("assignees", ["nobody"]),
    ],
)
def test_bad_fields_are_refused(db, actor, chores, field, value):
    with pytest.raises(Invalid):
        _rule(db, actor, chores, ct(2026, 11, 1), **{field: value})


def test_defaults(db, actor, chores):
    rule = _rule(db, actor, chores, ct(2026, 11, 1))
    assert rule.timezone == "America/Chicago"
    assert rule.lead_days == 14
    assert rule.missed_policy.value == "spawn_always"
    assert rule.dtstart == ct(2026, 11, 11)  # date-only dtstart = local midnight
    assert rule.next_run_at == ct(2026, 11, 11)
    assert rule.created_by == actor.account_id


def test_until_in_utc_or_local_date_both_work(db, actor, chores):
    now = ct(2026, 11, 1)
    a = _rule(db, actor, chores, now, rrule="FREQ=DAILY;UNTIL=20261113T060000Z")
    b = _rule(db, actor, chores, now, rrule="FREQ=DAILY;UNTIL=20261113")
    for rule in (a, b):
        got = [o.at for o in recurrences.occurrences(rule, ct(2026, 11, 1))]
        assert got == [ct(2026, 11, 11), ct(2026, 11, 12), ct(2026, 11, 13)]


# --- materialization -----------------------------------------------------------------------


def test_bulk_trash_rule_produces_exactly_the_next_14_days(db, actor, chores):
    now = ct(2026, 11, 5, 9)
    rule = _rule(db, actor, chores, now, dtstart="2026-11-11")
    recurrences.materialize(db, now)
    got = _instances(db, rule)
    # Every other Wednesday from Nov 11; the window is (now, now + 14 days] = to Nov 19 09:00.
    assert [i.occurrence_at for i in got] == [ct(2026, 11, 11)]
    assert got[0].due_at == ct(2026, 11, 11, 17)
    assert got[0].title == "Bulk trash Wed Nov 11"

    later = ct(2026, 11, 20, 9)  # window to Dec 4: adds Nov 25
    recurrences.materialize(db, later)
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 11), ct(2026, 11, 25)]
    assert rule.next_run_at == ct(2026, 12, 9)


def test_spawned_item_carries_the_rule(db, actor, chores):
    accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")
    now = ct(2026, 11, 10)
    rule = _rule(
        db,
        actor,
        chores,
        now,
        title_template="Trash {iso_date} {literal}",
        body_template_md="Bins out by {date}.",
        priority="high",
        tags=["Home", "chores"],
        assignees=["agent"],
    )
    [spawned] = recurrences.materialize(db, now)
    view = items.views(db, [spawned])[0]
    assert view["title"] == "Trash 2026-11-11 {literal}"
    assert view["body_md"] == "Bins out by Wed Nov 11."
    assert view["priority"] == "high"
    assert view["tags"] == ["chores", "home"]
    assert [a["handle"] for a in view["assignees"]] == ["agent"]
    assert view["recurrence_id"] == rule.id
    assert view["status"]["name"] == "To do"

    types = [
        e.type
        for e in db.scalars(select(Event).where(Event.item_id == spawned.id).order_by(Event.id))
    ]
    assert types[0] == "item.created"
    assert "recurrence.spawned" in types
    events = list(db.scalars(select(Event).where(Event.item_id == spawned.id)))
    assert {(e.actor_id, e.source) for e in events} == {(actor.account_id, Source.system)}
    assert {e.session_ref for e in events} == {f"recurrence:{rule.id}"}
    spawned_ev = next(e for e in events if e.type == "recurrence.spawned")
    assert spawned_ev.subject_id == rule.id


def test_rrule_with_a_time_uses_that_time_for_due(db, actor, chores):
    now = ct(2026, 11, 10)
    timed = _rule(db, actor, chores, now, rrule="FREQ=DAILY;BYHOUR=9", dtstart="2026-11-11")
    at_dtstart = _rule(db, actor, chores, now, rrule="FREQ=DAILY", dtstart="2026-11-11T07:30")
    recurrences.materialize(db, now)
    assert _instances(db, timed)[0].due_at == ct(2026, 11, 11, 9)
    assert _instances(db, timed)[0].occurrence_at == ct(2026, 11, 11, 9)
    assert _instances(db, at_dtstart)[0].due_at == ct(2026, 11, 11, 7, 30)


def test_materialize_twice_is_idempotent(db, actor, chores):
    now = ct(2026, 11, 10)
    rule = _rule(db, actor, chores, now, rrule="FREQ=DAILY")
    first = recurrences.materialize(db, now)
    events = db.scalar(select(func.count()).select_from(Event))
    assert len(first) == 14  # Nov 11 .. Nov 24 00:00 local (now + 14 days = Nov 24 00:00)
    assert recurrences.materialize(db, now) == []
    assert recurrences.materialize(db, now + timedelta(minutes=1)) == []
    assert len(_instances(db, rule)) == 14
    assert db.scalar(select(func.count()).select_from(Event)) == events


def test_database_refuses_a_second_instance_of_one_occurrence(db, actor, chores):
    now = ct(2026, 11, 10)
    rule = _rule(db, actor, chores, now)
    [item] = recurrences.materialize(db, now)
    db.rollback()
    with pytest.raises(IntegrityError), db.get_bind().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO items (id, list_id, kind, title, body_md, position, created_by, "
                "created_at, updated_at, recurrence_id, occurrence_at) SELECT 'dup000000000', "
                "list_id, kind, title, body_md, 99, created_by, created_at, updated_at, "
                "recurrence_id, occurrence_at FROM items WHERE id = :id"
            ),
            {"id": item.id},
        )
    assert len(_instances(db, rule)) == 1


def test_no_backfill_before_the_day_the_rule_was_created(db, actor, chores):
    now = ct(2026, 11, 20, 12)
    rule = _rule(db, actor, chores, now, rrule="FREQ=DAILY", dtstart="2026-11-01", lead_days=0)
    recurrences.materialize(db, now)
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 20)]


def test_completing_an_instance_never_touches_the_rule(db, actor, chores):
    now = ct(2026, 11, 10)
    rule = _rule(db, actor, chores, now)
    [item] = recurrences.materialize(db, now)
    before = recurrences.recurrence_view(db, rule, now)
    _complete(db, actor, item)
    rule_events = db.scalars(
        select(Event.type).where(Event.subject_id == rule.id, Event.item_id.is_(None))
    )
    assert list(rule_events) == ["recurrence.created"]
    assert recurrences.recurrence_view(db, rule, now) == before


# --- missed policies -----------------------------------------------------------------------


def test_spawn_always_spawns_while_an_instance_is_open(db, actor, chores):
    rule = _rule(db, actor, chores, ct(2026, 11, 10), rrule="FREQ=WEEKLY", lead_days=0)
    recurrences.materialize(db, ct(2026, 11, 11, 1))
    recurrences.materialize(db, ct(2026, 11, 18, 1))  # Nov 11 still open (and overdue)
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 11), ct(2026, 11, 18)]


def test_skip_if_open_waits_while_open_and_not_yet_due(db, actor, chores):
    rule = _rule(
        db, actor, chores, ct(2026, 11, 10), rrule="FREQ=WEEKLY", missed_policy="skip_if_open"
    )
    recurrences.materialize(db, ct(2026, 11, 10))  # window holds Nov 11 and Nov 18
    [first] = _instances(db, rule)
    assert first.occurrence_at == ct(2026, 11, 11)
    assert rule.next_run_at == ct(2026, 11, 18)  # deferred, not skipped

    _complete(db, actor, first)  # done in time: the next one appears at once
    recurrences.materialize(db, ct(2026, 11, 12))
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 11), ct(2026, 11, 18)]


def test_skip_if_open_skips_an_occurrence_that_arrives_while_one_is_open(db, actor, chores):
    rule = _rule(
        db,
        actor,
        chores,
        ct(2026, 11, 10),
        rrule="FREQ=WEEKLY",
        missed_policy="skip_if_open",
        lead_days=7,
    )
    recurrences.materialize(db, ct(2026, 11, 10))
    recurrences.materialize(db, ct(2026, 11, 18, 18))  # Nov 18 17:00 passed; Nov 11 still open
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 11)]
    skipped = list(db.scalars(select(Event).where(Event.type == "recurrence.skipped")))
    assert [e.after_json["occurrence_at"] for e in skipped] == ["2026-11-18T06:00:00.000000Z"]
    assert rule.next_run_at == ct(2026, 11, 25)  # Nov 25 waits: it is not due yet

    _complete(db, actor, _instances(db, rule)[0])
    recurrences.materialize(db, ct(2026, 11, 19))
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 11), ct(2026, 11, 25)]


def test_roll_forward_moves_the_missed_instance_instead_of_spawning(db, actor, chores):
    rule = _rule(
        db,
        actor,
        chores,
        ct(2026, 11, 10),
        rrule="FREQ=WEEKLY",
        missed_policy="roll_forward",
        lead_days=7,
    )
    recurrences.materialize(db, ct(2026, 11, 10))
    [only] = _instances(db, rule)
    assert only.due_at == ct(2026, 11, 11, 17)

    recurrences.materialize(db, ct(2026, 11, 11, 12))  # Nov 18 in window, Nov 11 not yet missed
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 11)]

    recurrences.materialize(db, ct(2026, 11, 11, 18))  # missed: rolls to Nov 18
    [only] = _instances(db, rule)
    assert (only.occurrence_at, only.due_at) == (ct(2026, 11, 18), ct(2026, 11, 18, 17))
    rolled = list(
        db.scalars(
            select(Event)
            .where(Event.item_id == only.id, Event.field == "due_at")
            .order_by(Event.id)
        )
    )
    assert [(e.type, e.before_json, e.after_json) for e in rolled] == [
        ("item.updated", "2026-11-11T23:00:00.000000Z", "2026-11-18T23:00:00.000000Z")
    ]
    assert rolled[0].session_ref == f"recurrence:{rule.id}"
    note = db.scalar(select(Event).where(Event.type == "recurrence.rolled_forward"))
    assert note.item_id == only.id and note.subject_id == rule.id

    _complete(db, actor, only)
    recurrences.materialize(db, ct(2026, 11, 19))
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 18), ct(2026, 11, 25)]


def test_roll_forward_after_downtime_ends_with_one_instance(db, actor, chores):
    rule = _rule(
        db,
        actor,
        chores,
        ct(2026, 11, 10),
        rrule="FREQ=DAILY",
        missed_policy="roll_forward",
        lead_days=0,
    )
    recurrences.materialize(db, ct(2026, 11, 11, 1))
    recurrences.materialize(db, ct(2026, 11, 20, 1))  # nine days down
    [only] = _instances(db, rule)
    assert only.occurrence_at == ct(2026, 11, 20)


def test_archived_instance_does_not_count_as_open(db, actor, chores):
    rule = _rule(
        db, actor, chores, ct(2026, 11, 10), rrule="FREQ=WEEKLY", missed_policy="skip_if_open"
    )
    recurrences.materialize(db, ct(2026, 11, 10))
    items.archive_item(db, actor, _instances(db, rule)[0].id)
    recurrences.materialize(db, ct(2026, 11, 10))
    assert len(_instances(db, rule)) == 2


# --- lifecycle -----------------------------------------------------------------------------


def test_pause_stops_and_resume_restarts_from_today(db, actor, chores):
    rule = _rule(db, actor, chores, ct(2026, 11, 1), rrule="FREQ=DAILY", lead_days=0)
    recurrences.pause_recurrence(db, actor, rule.id, now=ct(2026, 11, 1))
    assert recurrences.materialize(db, ct(2026, 11, 15)) == []
    recurrences.resume_recurrence(db, actor, rule.id, now=ct(2026, 11, 20, 8))
    recurrences.materialize(db, ct(2026, 11, 20, 8))
    assert [i.occurrence_at for i in _instances(db, rule)] == [ct(2026, 11, 20)]


def test_end_stops_for_good(db, actor, chores):
    rule = _rule(db, actor, chores, ct(2026, 11, 1), rrule="FREQ=DAILY")
    recurrences.end_recurrence(db, actor, rule.id)
    assert rule.ended_at is not None and rule.next_run_at is None
    assert recurrences.materialize(db, ct(2026, 11, 15)) == []
    with pytest.raises(Invalid):
        recurrences.resume_recurrence(db, actor, rule.id)
    with pytest.raises(Invalid):
        recurrences.run_now(db, actor, rule.id)


def test_run_now_materializes_immediately_as_the_caller(db, actor, chores):
    rule = _rule(db, actor, chores, ct(2026, 11, 10))
    spawned = recurrences.run_now(db, actor, rule.id, now=ct(2026, 11, 10))
    assert [i.occurrence_at for i in spawned] == [ct(2026, 11, 11)]
    ev = db.scalar(select(Event).where(Event.type == "recurrence.spawned"))
    assert ev.source == Source.api


def test_update_reanchors_without_duplicating(db, actor, chores):
    now = ct(2026, 11, 10)
    rule = _rule(db, actor, chores, now, rrule="FREQ=WEEKLY;BYDAY=WE")
    recurrences.materialize(db, now)  # Nov 11, Nov 18
    recurrences.update_recurrence(
        db,
        actor,
        rule.id,
        {"rrule": "FREQ=WEEKLY;BYDAY=WE,TH", "title_template": "T {date}"},
        now=now,
    )
    recurrences.materialize(db, now)
    got = [(i.occurrence_at, i.title) for i in _instances(db, rule)]
    assert got == [
        (ct(2026, 11, 11), "Bulk trash Wed Nov 11"),
        (ct(2026, 11, 18), "Bulk trash Wed Nov 18"),
        (ct(2026, 11, 19), "T Thu Nov 19"),
    ]
    updated = db.scalars(select(Event.field).where(Event.type == "recurrence.updated"))
    assert set(updated) == {"rrule", "title_template"}


def test_next_occurrences(db, actor, chores):
    now = ct(2026, 11, 5)
    rule = _rule(db, actor, chores, now)
    view = recurrences.recurrence_view(db, rule, now)
    nxt = view["next_occurrences"]
    assert len(nxt) == 5
    assert nxt[0] == {
        "local": "2026-11-11T00:00:00-06:00",
        "utc": "2026-11-11T06:00:00Z",
        "due_at": "2026-11-11T23:00:00Z",
    }
    assert [n["local"][:10] for n in nxt] == [
        "2026-11-11",
        "2026-11-25",
        "2026-12-09",
        "2026-12-23",
        "2027-01-06",
    ]


def test_one_failing_rule_does_not_block_the_others(db, actor, chores, monkeypatch):
    now = ct(2026, 11, 10)
    bad = _rule(db, actor, chores, now)
    good = _rule(db, actor, chores, now)
    real = recurrences._spawn

    def spawn(cx, rule, occ):
        if rule.id == bad.id:
            raise RuntimeError("boom")
        return real(cx, rule, occ)

    monkeypatch.setattr(recurrences, "_spawn", spawn)
    recurrences.materialize(db, now)
    assert _instances(db, bad) == []
    assert len(_instances(db, good)) == 1
    monkeypatch.setattr(recurrences, "_spawn", real)
    recurrences.materialize(db, now)  # retried on the next tick
    assert len(_instances(db, bad)) == 1


def test_before_due_alerts_follow_to_the_next_instance(db, actor, chores):
    from slate.models import Reminder
    from slate.service import reminders

    rule = _rule(db, actor, chores, ct(2026, 11, 10), rrule="FREQ=WEEKLY", lead_days=0)
    recurrences.materialize(db, ct(2026, 11, 11, 1))
    (first,) = _instances(db, rule)
    reminders.add_reminder(db, actor, first.id, before_minutes=60)
    reminders.add_reminder(db, actor, first.id, before_minutes=1440)
    reminders.add_reminder(db, actor, first.id, at="2026-11-11T09:00:00-06:00")  # fixed time

    recurrences.materialize(db, ct(2026, 11, 18, 1))
    second = _instances(db, rule)[1]
    copied = db.scalars(select(Reminder).where(Reminder.item_id == second.id)).all()
    assert sorted(r.before_minutes for r in copied) == [60, 1440]
    assert all(r.remind_at is None and r.account_id == actor.account_id for r in copied)
