"""A rule that can never occur is refused, and no walk of a rule is unbounded or locked."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from slate.models import MissedPolicy, Recurrence, RecurTrigger, Source
from slate.service import accounts, core, lists, recurrences, sections
from slate.service.core import Actor
from slate.service.errors import Invalid

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)

# Each of these matches no day at all (February 30; every 7 days from a Thursday, only on
# Mondays; the tenth Monday of a month).
NEVER = [
    ("FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30", "2026-01-01"),
    ("FREQ=DAILY;INTERVAL=7;BYDAY=MO", "2026-01-01"),
    ("FREQ=MONTHLY;BYDAY=MO;BYSETPOS=10", "2026-01-01"),
    ("FREQ=YEARLY;BYMONTH=4;BYMONTHDAY=31;COUNT=3", "2026-01-01"),
]


@pytest.fixture
def actor(db) -> Actor:
    account, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    return Actor(account_id=account.id, source=Source.api)


@pytest.fixture
def chores(db, actor):
    section = sections.create_section(db, actor, name="Home", audience="personal")
    return lists.create_list(db, actor, section_id=section.id, name="Chores")


@pytest.mark.parametrize(("rrule", "dtstart"), NEVER)
def test_a_rule_that_never_occurs_is_refused_quickly(db, actor, chores, rrule, dtstart):
    started = time.perf_counter()
    with pytest.raises(Invalid, match="never occurs"):
        recurrences.create_recurrence(
            db, actor, list_id=chores.id, title_template="x", rrule=rrule, dtstart=dtstart, now=NOW
        )
    with pytest.raises(Invalid, match="never occurs"):
        recurrences.preview(rrule, dtstart=dtstart, now=NOW)
    assert time.perf_counter() - started < 1.5


def test_update_to_a_never_rule_is_refused_and_changes_nothing(db, actor, chores):
    rule = recurrences.create_recurrence(
        db, actor, list_id=chores.id, title_template="x", rrule="FREQ=WEEKLY", now=NOW
    )
    with pytest.raises(Invalid, match="never occurs"):
        recurrences.update_recurrence(
            db, actor, rule.id, {"rrule": "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30"}, now=NOW
        )
    db.refresh(rule)
    assert rule.rrule == "FREQ=WEEKLY"


def test_rare_rules_that_do_occur_are_kept(db, actor, chores):
    # February 29 that falls on a Monday: 2044 is the next one.
    rule = recurrences.create_recurrence(
        db,
        actor,
        list_id=chores.id,
        title_template="x",
        rrule="FREQ=DAILY;BYMONTH=2;BYMONTHDAY=29;BYDAY=MO",
        dtstart="2026-01-01",
        now=NOW,
    )
    assert rule.next_run_at is not None
    assert rule.next_run_at.astimezone(CT).date().isoformat() == "2044-02-29"


def test_a_stored_never_rule_is_cheap_to_view(db, actor, chores):
    """A rule saved before the check existed: every view, list and tick stays cheap."""
    rule = Recurrence(
        id="never-rule",
        list_id=chores.id,
        title_template="x",
        rrule="FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30",
        dtstart=datetime(2026, 1, 1, 6, tzinfo=UTC),
        timezone="America/Chicago",
        lead_days=14,
        missed_policy=MissedPolicy.spawn_always,
        trigger=RecurTrigger.schedule,
        skip_weekends=False,
        reopen=False,
        copy_checklists=True,
        created_by=actor.account_id,
    )
    started = time.perf_counter()
    for _ in range(10):
        assert recurrences.recurrence_view(db, rule, NOW)["next_occurrences"] == []
    assert time.perf_counter() - started < 1.0


def test_update_walks_the_rule_before_taking_the_write_lock(db, actor, chores, monkeypatch):
    rule = recurrences.create_recurrence(
        db, actor, list_id=chores.id, title_template="x", rrule="FREQ=WEEKLY", now=NOW
    )
    held = []
    real = recurrences._anchor

    def anchor(*args, **kwargs):
        held.append(core._write_lock._is_owned())
        return real(*args, **kwargs)

    monkeypatch.setattr(recurrences, "_anchor", anchor)
    recurrences.update_recurrence(db, actor, rule.id, {"rrule": "FREQ=DAILY"}, now=NOW)
    assert held == [False]
    assert rule.rrule == "FREQ=DAILY"
    assert rule.next_run_at is not None
