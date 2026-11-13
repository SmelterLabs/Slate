"""apply_change(): the single change path, and the guard that makes it the only one."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import insert, select, text, update

from slate.models import Account, Event, Section, Source, Tag, Token
from slate.service import accounts
from slate.service.bus import bus
from slate.service.core import (
    Actor,
    WriteOutsideChange,
    WriteWithoutEvent,
    apply_change,
)


@pytest.fixture
def alex(db) -> Actor:
    account, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    return Actor(account_id=account.id, source=Source.api, session_ref="sess-1")


def _events(db, type_: str | None = None) -> list[Event]:
    q = select(Event).order_by(Event.id)
    if type_:
        q = q.where(Event.type == type_)
    return list(db.scalars(q))


# --- the rule: no write without an event ---------------------------------------------------


def test_add_outside_apply_change_is_refused(db):
    db.add(Account(handle="rogue", display_name="Rogue", kind="agent"))
    with pytest.raises(WriteOutsideChange):
        db.commit()


def test_update_outside_apply_change_is_refused(db, alex):
    account = db.get(Account, alex.account_id)
    account.display_name = "Someone else"
    with pytest.raises(WriteOutsideChange):
        db.flush()


def test_delete_outside_apply_change_is_refused(db, alex):
    db.delete(db.get(Account, alex.account_id))
    with pytest.raises(WriteOutsideChange):
        db.flush()


def test_bulk_dml_outside_apply_change_is_refused(db, alex):
    with pytest.raises(WriteOutsideChange):
        db.execute(update(Account).values(display_name="x"))
    with pytest.raises(WriteOutsideChange):
        db.execute(insert(Account).values(id="x" * 12, handle="x", display_name="x", kind="agent"))


def test_write_inside_scope_without_event_is_refused(db, alex):
    with pytest.raises(WriteWithoutEvent), apply_change(db, alex):
        db.add(Section(name="Personal", slug="personal", audience="personal"))
    assert db.scalar(select(Section)) is None  # rolled back


def test_bulk_write_inside_scope_without_event_is_refused(db, alex):
    with pytest.raises(WriteWithoutEvent), apply_change(db, alex):
        db.execute(update(Account).values(display_name="x"))
    assert db.get(Account, alex.account_id).display_name == "Alex"


RAW_WRITES = {
    "text()": lambda db: db.execute(
        text("INSERT INTO tags (id, name) VALUES ('vvvvvvvvvvvv', 'raw')")
    ),
    "session.connection()": lambda db: db.connection().execute(
        insert(Tag).values(id="w" * 12, name="conn")
    ),
    "bulk_insert_mappings": lambda db: db.bulk_insert_mappings(
        Tag, [{"id": "y" * 12, "name": "bulk"}]
    ),
    "exec_driver_sql": lambda db: db.connection().exec_driver_sql(
        "UPDATE accounts SET display_name = 'raw'"
    ),
}


@pytest.mark.parametrize("how", sorted(RAW_WRITES))
def test_a_raw_write_outside_apply_change_is_refused(db, alex, how):
    with pytest.raises(WriteOutsideChange):
        RAW_WRITES[how](db)
    db.rollback()
    assert db.scalars(select(Tag)).all() == []
    assert db.get(Account, alex.account_id).display_name == "Alex"


@pytest.mark.parametrize("how", sorted(RAW_WRITES))
def test_a_raw_write_inside_apply_change_still_needs_an_event(db, alex, how):
    with pytest.raises(WriteWithoutEvent), apply_change(db, alex):
        RAW_WRITES[how](db)
    assert db.scalars(select(Tag)).all() == []


def test_a_raw_read_is_still_allowed(db, alex):
    assert db.execute(text("SELECT count(*) FROM accounts")).scalar() == 1
    assert db.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_events_cannot_be_edited_or_deleted_through_the_orm(db, alex):
    ev = _events(db)[0]
    with pytest.raises(WriteOutsideChange, match="append-only"), apply_change(db, alex) as cx:
        ev.type = "rewritten"
        cx.record("noise")
    db.expire_all()
    ev = _events(db)[0]
    with pytest.raises(WriteOutsideChange, match="append-only"), apply_change(db, alex) as cx:
        db.delete(ev)
        cx.record("noise")


def test_token_last_used_at_is_the_only_bookkeeping_exemption(db, alex):
    token = db.scalar(select(Token))
    from slate.models import utcnow

    token.last_used_at = utcnow()
    db.commit()  # allowed
    token.label = "renamed"
    with pytest.raises(WriteOutsideChange):
        db.commit()


# --- what apply_change records -------------------------------------------------------------


def test_create_records_snapshot_actor_source_and_session_ref(db, alex):
    with apply_change(db, alex) as cx:
        section = cx.create(
            Section(name="Personal", slug="personal", audience="personal"), "section.created"
        )
    [ev] = _events(db, "section.created")
    assert ev.actor_id == alex.account_id
    assert ev.source == Source.api
    assert ev.session_ref == "sess-1"
    assert ev.subject_id == section.id
    assert ev.item_id is None
    assert ev.before_json is None
    assert ev.after_json["slug"] == "personal"
    assert ev.after_json["audience"] == "personal"
    assert ev.after_json["created_at"].endswith("Z")


def test_set_records_before_and_after(db, alex):
    with apply_change(db, alex) as cx:
        section = cx.create(Section(name="Personal", slug="p", audience="personal"), "s.created")
    with apply_change(db, alex) as cx:
        assert cx.set(section, "name", "Home", "section.updated") is True
        assert cx.set(section, "position", 0, "section.updated") is False  # unchanged: no event
    [ev] = _events(db, "section.updated")
    assert (ev.field, ev.before_json, ev.after_json) == ("name", "Personal", "Home")
    assert ev.subject_id == section.id
    db.expire_all()
    assert db.get(Section, section.id).name == "Home"


def test_delete_records_last_snapshot(db, alex):
    with apply_change(db, alex) as cx:
        section = cx.create(Section(name="Temp", slug="t", audience="ai"), "s.created")
    with apply_change(db, alex) as cx:
        cx.delete(section, "section.deleted")
    [ev] = _events(db, "section.deleted")
    assert ev.before_json["name"] == "Temp"
    assert ev.after_json is None
    assert db.get(Section, section.id) is None


def test_record_with_covers_allows_a_custom_write(db, alex):
    with apply_change(db, alex) as cx:
        s = Section(name="A", slug="a", audience="ai")
        db.add(s)
        cx.record("section.imported", after={"slug": "a"}, covers=[s])
    assert _events(db, "section.imported")


def test_secrets_never_enter_event_payloads(db, alex):
    [ev] = _events(db, "token.created")
    assert "token_hash" not in ev.after_json
    assert ev.after_json["scopes"] == "admin"


def test_exception_rolls_back_writes_and_events(db, alex):
    before = len(_events(db))
    with pytest.raises(RuntimeError, match="boom"), apply_change(db, alex) as cx:
        cx.create(Section(name="A", slug="a", audience="ai"), "section.created")
        raise RuntimeError("boom")
    assert len(_events(db)) == before
    assert db.scalar(select(Section)) is None


def test_nested_scopes_join_and_commit_once(db, alex):
    with apply_change(db, alex) as outer:
        outer.create(Section(name="A", slug="a", audience="ai"), "section.created")
        with apply_change(db, alex) as inner:
            assert inner is outer
            inner.create(Section(name="B", slug="b", audience="ai"), "section.created")
        assert db.in_transaction()
    assert len(_events(db, "section.created")) == 2


def test_nested_scope_with_a_different_actor_is_refused(db, alex):
    other = Actor(account_id=alex.account_id, source=Source.mcp)
    with (
        pytest.raises(RuntimeError, match="different actor"),
        apply_change(db, alex),
        apply_change(db, other),
    ):
        pass


def test_event_ids_follow_commit_order(db, alex):
    with apply_change(db, alex) as cx:
        for n in range(20):
            cx.create(Section(name=str(n), slug=f"s{n}", audience="ai"), "section.created")
    evs = _events(db, "section.created")
    assert [e.after_json["name"] for e in evs] == [str(n) for n in range(20)]


async def test_committed_events_are_published_to_the_bus(db, alex):
    async with bus.subscribe() as sub:
        with apply_change(db, alex) as cx:
            cx.create(Section(name="A", slug="a", audience="ai"), "section.created")
        msg = await asyncio.wait_for(anext(sub), 1)
    assert msg["type"] == "section.created"
    assert msg["actor_id"] == alex.account_id
    assert msg["source"] == "api"
    assert msg["session_ref"] == "sess-1"
    assert msg["after"]["slug"] == "a"
    assert msg["at"].endswith("Z")


async def test_rolled_back_changes_are_not_published(db, alex):
    async with bus.subscribe() as sub:
        with pytest.raises(RuntimeError), apply_change(db, alex) as cx:
            cx.create(Section(name="A", slug="a", audience="ai"), "section.created")
            raise RuntimeError
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(sub), 0.1)
