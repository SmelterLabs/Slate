"""The checked-in migration builds the same schema the models describe, triggers included."""

from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from slate.models import TRIGGERS

ROOT = Path(__file__).resolve().parents[1]


def _alembic(db: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "SLATE_DATABASE_PATH": str(db)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_upgrade_on_empty_file_matches_models_then_downgrades(tmp_path):
    db = tmp_path / "slate.db"
    up = _alembic(db, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    check = _alembic(db, "check")
    assert check.returncode == 0, check.stdout + check.stderr

    with sqlite3.connect(db) as conn:
        triggers = {
            r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger'")
        }
    assert triggers == {sql for stmts in TRIGGERS.values() for sql in stmts}

    down = _alembic(db, "downgrade", "base")
    assert down.returncode == 0, down.stderr


BASELINE = "a7e2c9d4f1b3"
VERSIONS = ROOT / "alembic" / "versions"


def _baseline():
    [path] = VERSIONS.glob("*_baseline.py")
    spec = importlib.util.spec_from_file_location("baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_triggers_repeat_the_models_verbatim():
    # The baseline creates the triggers; it must carry the models' text verbatim.
    assert sorted(sql for stmts in TRIGGERS.values() for sql in stmts) == sorted(
        _baseline().TRIGGERS
    )


def test_history_starts_at_one_baseline():
    baseline = _baseline()
    assert baseline.revision == BASELINE and baseline.down_revision is None
    roots = [
        p.name
        for p in VERSIONS.glob("*.py")
        if "down_revision = None" in p.read_text(encoding="utf-8")
    ]
    assert roots == [Path(baseline.__file__).name]


def test_migrations_do_not_import_app_code():
    # A migration that imports `slate` changes meaning when the app changes.
    for path in VERSIONS.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not re.match(r"\s*(from|import) slate\b", line), f"{path.name}: {line}"


def test_a_database_older_than_the_baseline_is_refused_with_advice(tmp_path):
    db = tmp_path / "slate.db"
    assert _alembic(db, "upgrade", "head").returncode == 0
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE alembic_version SET version_num = 'fbf0dc707d9f'")
    up = _alembic(db, "upgrade", "head")
    assert up.returncode != 0
    assert "older than this version's first migration" in up.stderr
    assert "Slate 0.1.0" in up.stderr


def test_a_database_at_the_baseline_or_head_needs_nothing(tmp_path):
    db = tmp_path / "slate.db"
    assert _alembic(db, "upgrade", "head").returncode == 0
    # Stamped at the baseline (as a database the step-by-step history brought there is), it
    # upgrades to head; at head, upgrading again changes nothing.
    assert _alembic(db, "downgrade", BASELINE).returncode == 0
    assert _alembic(db, "stamp", BASELINE).returncode == 0
    up = _alembic(db, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    with sqlite3.connect(db) as conn:
        before = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    again = _alembic(db, "upgrade", "head")
    assert again.returncode == 0, again.stderr
    assert "Running upgrade" not in again.stderr
    with sqlite3.connect(db) as conn:
        after = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    assert before == after


def test_upgrade_keeps_rows_that_other_tables_point_at(tmp_path):
    """Every revision after the baseline must work on a file that already holds rows.

    SQLite swaps a table by dropping it, which fails with foreign keys on once another table
    points at a row, so the upgrade is run on a file that holds rows, not an empty one.
    """
    db = tmp_path / "slate.db"
    up = _alembic(db, "upgrade", BASELINE)
    assert up.returncode == 0, up.stderr

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO accounts (id, handle, display_name, kind, created_at) "
            "VALUES ('acc000000001', 'alex', 'Alex', 'person', '2026-10-01T00:00:00.000000Z')"
        )
        conn.execute(
            "INSERT INTO tokens (id, account_id, token_hash, scopes, created_at) "
            "VALUES ('tok000000001', 'acc000000001', 'h', 'admin', '2026-10-01T00:00:00.000000Z')"
        )

    head = _alembic(db, "upgrade", "head")
    assert head.returncode == 0, head.stderr

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT count(*) FROM accounts").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM tokens").fetchone() == (1,)
        leftovers = conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp%'")
        assert leftovers.fetchall() == []


def _row(conn: sqlite3.Connection, table: str, **values: object) -> None:
    """Insert a row, filling every other NOT NULL column without a default with "x"."""
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for c in cols:
        if c[3] and c[4] is None and c[1] not in values:
            values[c[1]] = "2026-10-01T00:00:00.000000Z" if c[1].endswith("_at") else "x"
    names = list(values)
    marks = ", ".join("?" * len(names))
    conn.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({marks})", list(values.values())
    )


DEAD_COLUMNS = {
    "comments": {"anchor_version_id", "anchor_start", "anchor_end", "anchor_quote"},
    "attachments": {"path"},
    "work_leases": {"from_pool"},
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_dead_columns_and_the_doc_item_kind_are_dropped_and_rows_kept(tmp_path):
    """Columns nothing reads go and a `doc` item becomes a task; the rows stay, the item
    triggers survive the rebuild of `items`, and downgrade brings the columns back."""
    db = tmp_path / "slate.db"
    assert _alembic(db, "upgrade", "b4f8d2e6a1c9").returncode == 0
    with sqlite3.connect(db) as conn:
        _row(conn, "accounts", id="acc000000001", handle="alex", kind="person", role="owner")
        _row(conn, "sections", id="sec000000001", slug="home", audience="work")
        _row(conn, "lists", id="lst000000001", section_id="sec000000001", slug="l")
        _row(conn, "statuses", id="sta000000001", list_id="lst000000001", kind="open")
        _row(
            conn,
            "items",
            id="itm000000001",
            list_id="lst000000001",
            status_id="sta000000001",
            kind="task",
            priority="normal",
            created_by="acc000000001",
        )
        _row(
            conn,
            "items",
            id="itm000000002",
            list_id="lst000000001",
            status_id="sta000000001",
            kind="doc",
            created_by="acc000000001",
        )
        _row(conn, "comments", id="com000000001", item_id="itm000000001", author_id="acc000000001")
        _row(
            conn,
            "attachments",
            id="att000000001",
            item_id="itm000000001",
            uploaded_by="acc000000001",
            size=1,
            path="db:att000000001",
        )
        _row(conn, "work_leases", item_id="itm000000001", account_id="acc000000001", from_pool=1)
    up = _alembic(db, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    with sqlite3.connect(db) as conn:
        for table, gone in DEAD_COLUMNS.items():
            assert not gone & _columns(conn, table), table
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT DISTINCT kind FROM items").fetchall() == [("task",)]
        with pytest.raises(sqlite3.IntegrityError):
            _row(conn, "items", id="itm000000003", list_id="lst000000001", kind="doc")
        triggers = {
            r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger'")
        }
        assert triggers == {sql for stmts in TRIGGERS.values() for sql in stmts}
    down = _alembic(db, "downgrade", "b4f8d2e6a1c9")
    assert down.returncode == 0, down.stderr
    with sqlite3.connect(db) as conn:
        for table, gone in DEAD_COLUMNS.items():
            assert gone <= _columns(conn, table), table
        assert conn.execute("SELECT path FROM attachments").fetchone() == ("db:att000000001",)
