"""Property tests: day-by-day materialization equals what `rrulestr` says, across DST.

America/Chicago leaves daylight time on 2026-11-01 and enters it on 2027-03-14. Each example
gets its own in-memory database (hypothesis cannot reuse function-scoped fixtures).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from hypothesis import example, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from slate.db import Base, make_engine
from slate.models import Item, Source
from slate.service import accounts, lists, recurrences, sections
from slate.service.core import Actor

CT = ZoneInfo("America/Chicago")
DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
SPAN_DAYS = 140


@contextmanager
def fresh_db():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        account, _, _ = accounts.bootstrap(session, handle="alex", display_name="Alex")
        actor = Actor(account_id=account.id, source=Source.api)
        section = sections.create_section(session, actor, name="Home", audience="personal")
        chores = lists.create_list(session, actor, section_id=section.id, name="Chores")
        yield session, actor, chores
    finally:
        session.close()
        engine.dispose()


@st.composite
def rrules(draw) -> str:
    freq = draw(st.sampled_from(["DAILY", "WEEKLY", "MONTHLY"]))
    parts = [f"FREQ={freq}", f"INTERVAL={draw(st.integers(1, 6))}"]
    byday = draw(st.lists(st.sampled_from(DAYS), unique=True, max_size=3))
    if byday:
        parts.append("BYDAY=" + ",".join(byday))
    return ";".join(parts)


def _expected(rule_text: str, start: date, until: datetime) -> list[datetime]:
    out = []
    for naive in rrulestr(rule_text, dtstart=datetime.combine(start, time(0))):
        at = naive.replace(tzinfo=CT).astimezone(UTC)
        if at > until:
            break
        out.append(at)
    return out


@settings(max_examples=100, deadline=None)
@given(
    rule_text=rrules(),
    start=st.dates(date(2026, 1, 1), date(2027, 12, 31)),
    days_before=st.integers(0, 3),
    hour=st.integers(0, 23),
    lead_days=st.integers(0, 14),
)
@example("FREQ=DAILY;INTERVAL=1", date(2026, 10, 25), 0, 1, 14)  # through 2026-11-01
@example("FREQ=WEEKLY;INTERVAL=2;BYDAY=WE", date(2026, 10, 28), 0, 9, 14)
@example("FREQ=DAILY;INTERVAL=1", date(2027, 3, 7), 0, 23, 3)  # through 2027-03-14
@example("FREQ=WEEKLY;INTERVAL=1;BYDAY=SU", date(2027, 3, 1), 2, 2, 0)
def test_day_by_day_materialization_matches_rrulestr(
    rule_text, start, days_before, hour, lead_days
):
    with fresh_db() as (db, actor, chores):
        first_now = datetime.combine(start - timedelta(days=days_before), time(hour), tzinfo=CT)
        rule = recurrences.create_recurrence(
            db,
            actor,
            list_id=chores.id,
            title_template="Chore {date}",
            rrule=rule_text,
            dtstart=start.isoformat(),
            lead_days=lead_days,
            now=first_now,
        )
        horizon = None
        for day in range(SPAN_DAYS):
            # Step by local calendar day, so the wall-clock hour is the same every tick.
            now = datetime.combine(first_now.date() + timedelta(days=day), time(hour), tzinfo=CT)
            recurrences.materialize(db, now.astimezone(UTC))
            horizon = now.astimezone(UTC) + timedelta(days=lead_days)

        got = list(db.scalars(select(Item).where(Item.recurrence_id == rule.id)))
        occurrences = sorted(i.occurrence_at for i in got)
        assert len(occurrences) == len(set(occurrences)), "duplicate instance"
        assert occurrences == _expected(rule_text, start, horizon)
        for item in got:
            local_occ = item.occurrence_at.astimezone(CT)
            local_due = item.due_at.astimezone(CT)
            assert local_occ.time() == time(0)
            assert local_due.time() == time(17, 0), item.due_at
            assert local_due.date() == local_occ.date()


def test_due_time_stays_17_00_local_across_both_transitions():
    """The DST evidence: the UTC due time shifts by an hour, the local one never does."""
    with fresh_db() as (db, actor, chores):
        rule = recurrences.create_recurrence(
            db,
            actor,
            list_id=chores.id,
            title_template="Daily {iso_date}",
            rrule="FREQ=DAILY",
            dtstart="2026-10-30",
            lead_days=0,
            now=datetime(2026, 10, 30, tzinfo=CT),
        )
        due = {}
        for d in [date(2026, 10, 30) + timedelta(days=n) for n in range(140)]:
            recurrences.materialize(db, datetime.combine(d, time(23), tzinfo=CT))
        for item in db.scalars(select(Item).where(Item.recurrence_id == rule.id)):
            due[item.title] = item.due_at
        assert len(due) == 140
        assert due["Daily 2026-10-31"] == datetime(2026, 10, 31, 22, tzinfo=UTC)  # CDT, -5
        assert due["Daily 2026-11-01"] == datetime(2026, 11, 1, 23, tzinfo=UTC)  # CST, -6
        assert due["Daily 2027-03-13"] == datetime(2027, 3, 13, 23, tzinfo=UTC)  # CST
        assert due["Daily 2027-03-14"] == datetime(2027, 3, 14, 22, tzinfo=UTC)  # CDT again
        assert {d.astimezone(CT).time() for d in due.values()} == {time(17, 0)}


def test_every_two_weeks_keeps_its_anchor_across_dst():
    """Bulk trash: every other Wednesday from 2026-10-28, through both transitions."""
    with fresh_db() as (db, actor, chores):
        rule = recurrences.create_recurrence(
            db,
            actor,
            list_id=chores.id,
            title_template="Bulk trash {date}",
            rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=WE",
            dtstart="2026-10-28",
            now=datetime(2026, 10, 20, tzinfo=CT),
        )
        for n in range(170):
            recurrences.materialize(db, datetime(2026, 10, 20, 8, tzinfo=CT) + timedelta(days=n))
        days = [
            i.occurrence_at.astimezone(CT).date()
            for i in db.scalars(
                select(Item).where(Item.recurrence_id == rule.id).order_by(Item.occurrence_at)
            )
        ]
        assert days[0] == date(2026, 10, 28)
        assert all(b - a == timedelta(days=14) for a, b in zip(days, days[1:], strict=False))
        assert all(d.weekday() == 2 for d in days)
        assert date(2027, 3, 17) in days  # first one after clocks spring forward
