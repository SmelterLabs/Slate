"""Schema constraints, checked at the database level."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, StatementError

from slate.db import engine
from slate.ids import new_event_id, new_id

PLAN_TABLES = {
    "accounts",
    "tokens",
    "sections",
    "lists",
    "statuses",
    "items",
    "item_assignees",
    "tags",
    "item_tags",
    "comments",
    "body_versions",
    "events",
    "recurrences",
    "webhooks",
    "deliveries",
    "attachments",
}

NOW = "2026-10-23T12:00:00.000000Z"


def _exec(sql: str, **params) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def _seed_list() -> None:
    """Raw SQL seed: these tests probe the database, not the service layer."""
    _exec(
        "INSERT INTO accounts (id, handle, display_name, kind, created_at) "
        "VALUES ('acct00000001', 'alex', 'Alex', 'person', :now)",
        now=NOW,
    )
    _exec(
        "INSERT INTO sections (id, name, slug, position, audience, created_at) "
        "VALUES ('sect00000001', 'Personal', 'personal', 0, 'personal', :now)",
        now=NOW,
    )
    _exec(
        "INSERT INTO lists (id, section_id, name, slug, position, created_at) "
        "VALUES ('list00000001', 'sect00000001', 'Todo', 'todo', 0, :now)",
        now=NOW,
    )


def _insert_item(item_id: str, parent_id: str | None = None, kind: str = "task") -> None:
    _exec(
        "INSERT INTO items (id, list_id, parent_id, kind, title, body_md, position, created_by, "
        "created_at, updated_at) VALUES (:id, 'list00000001', :parent, :kind, 't', '', 0, "
        "'acct00000001', :now, :now)",
        id=item_id,
        parent=parent_id,
        kind=kind,
        now=NOW,
    )


def test_every_plan_table_exists():
    assert set(inspect(engine).get_table_names()) >= PLAN_TABLES


def test_ids_are_12_char_url_safe_and_unique():
    ids = {new_id() for _ in range(2000)}
    assert len(ids) == 2000
    assert all(re.fullmatch(r"[0-9A-Za-z]{12}", i) for i in ids)


def test_event_ids_sort_in_creation_order():
    ids = [new_event_id() for _ in range(500)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 500
    assert all(re.fullmatch(r"[0-9A-Za-z]{12}", i) for i in ids)


def test_item_kind_is_task_or_doc():
    _seed_list()
    _insert_item("item00000001", kind="doc")
    with pytest.raises(IntegrityError):
        _insert_item("item00000002", kind="page")


def test_item_depth_is_at_most_four():
    """A top task and four levels of subtasks; the fifth level is refused by the trigger."""
    _seed_list()
    _insert_item("level0000000")
    for n in range(1, 5):
        _insert_item(f"level000000{n}", parent_id=f"level000000{n - 1}")
    with pytest.raises(IntegrityError, match="depth exceeds 4"):
        _insert_item("level0000005", parent_id="level0000004")


def test_item_with_children_can_move_while_the_tree_fits():
    """Reparenting is refused only when the task's new depth plus its subtree's height would
    pass four, or would make a cycle."""
    _seed_list()
    _insert_item("parent000001")
    _insert_item("child0000001", parent_id="parent000001")
    _insert_item("grand0000001", parent_id="child0000001")
    _insert_item("other0000001")
    _insert_item("other0000002", parent_id="other0000001")
    _insert_item("other0000003", parent_id="other0000002")
    # parent (height 2) under other0000001 (depth 0): depth 1 + 2 = 3, fine.
    _exec("UPDATE items SET parent_id = 'other0000001' WHERE id = 'parent000001'")
    # ...under other0000003 (depth 2): depth 3 + 2 = 5, refused.
    with pytest.raises(IntegrityError, match="depth exceeds 4"):
        _exec("UPDATE items SET parent_id = 'other0000003' WHERE id = 'parent000001'")
    # A task under its own grandchild is a cycle.
    with pytest.raises(IntegrityError, match="depth exceeds 4"):
        _exec("UPDATE items SET parent_id = 'grand0000001' WHERE id = 'parent000001'")


def test_item_cannot_parent_itself():
    _seed_list()
    _insert_item("item00000001")
    with pytest.raises(IntegrityError, match="depth"):
        _exec("UPDATE items SET parent_id = 'item00000001' WHERE id = 'item00000001'")


def test_status_kind_enum():
    _seed_list()
    _exec(
        "INSERT INTO statuses (id, list_id, name, kind, position) "
        "VALUES ('stat00000001', 'list00000001', 'Ready', 'open', 0)"
    )
    with pytest.raises(IntegrityError):
        _exec(
            "INSERT INTO statuses (id, list_id, name, kind, position) "
            "VALUES ('stat00000002', 'list00000001', 'Running', 'running', 1)"
        )


def test_tag_names_are_unique_and_lowercase():
    _exec("INSERT INTO tags (id, name) VALUES ('tag000000001', 'home')")
    with pytest.raises(IntegrityError):
        _exec("INSERT INTO tags (id, name) VALUES ('tag000000002', 'home')")
    with pytest.raises(IntegrityError):
        _exec("INSERT INTO tags (id, name) VALUES ('tag000000003', 'Home')")


def test_account_handle_unique_and_kind_enum():
    _seed_list()
    with pytest.raises(IntegrityError):
        _exec(
            "INSERT INTO accounts (id, handle, display_name, kind, created_at) "
            "VALUES ('acct00000002', 'alex', 'Dup', 'person', :now)",
            now=NOW,
        )
    with pytest.raises(IntegrityError):
        _exec(
            "INSERT INTO accounts (id, handle, display_name, kind, created_at) "
            "VALUES ('acct00000003', 'agent2', 'A', 'profile', :now)",
            now=NOW,
        )


def test_events_are_append_only_in_the_database():
    _seed_list()
    _exec(
        "INSERT INTO events (id, actor_id, source, type, at) "
        "VALUES ('evnt00000001', 'acct00000001', 'system', 'test', :now)",
        now=NOW,
    )
    with pytest.raises(IntegrityError, match="append-only"):
        _exec("UPDATE events SET type = 'changed' WHERE id = 'evnt00000001'")
    with pytest.raises(IntegrityError, match="append-only"):
        _exec("DELETE FROM events WHERE id = 'evnt00000001'")


def test_event_requires_actor_and_valid_source():
    _seed_list()
    with pytest.raises(IntegrityError):
        _exec(
            "INSERT INTO events (id, actor_id, source, type, at) "
            "VALUES ('evnt00000002', NULL, 'api', 'test', :now)",
            now=NOW,
        )
    with pytest.raises(IntegrityError):
        _exec(
            "INSERT INTO events (id, actor_id, source, type, at) "
            "VALUES ('evnt00000003', 'acct00000001', 'runtime', 'test', :now)",
            now=NOW,
        )


def test_utc_datetime_round_trips_as_iso_z():
    from sqlalchemy.orm import Session

    from slate.models import Account

    _exec(
        "INSERT INTO accounts (id, handle, display_name, kind, created_at) "
        "VALUES ('acct00000009', 'agent', 'Agent', 'agent', :now)",
        now=NOW,
    )
    with Session(engine) as s:
        acct = s.get(Account, "acct00000009")
        assert acct.created_at == datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def test_utc_datetime_rejects_naive():
    from slate.models import UTCDateTime

    with pytest.raises((ValueError, StatementError)):
        UTCDateTime().process_bind_param(datetime(2026, 1, 1), None)
