"""The checked-in migration builds the same schema the models describe, triggers included."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

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


def test_migration_triggers_repeat_the_models_verbatim():
    # The newest migration that (re)writes the triggers must carry the models' text verbatim.
    [path] = (ROOT / "alembic" / "versions").glob("*_nested_subtasks.py")
    spec = importlib.util.spec_from_file_location("nested_subtasks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert [sql for stmts in TRIGGERS.values() for sql in stmts] == module.TRIGGERS


def test_upgrade_keeps_rows_that_other_tables_point_at(tmp_path):
    """Rebuilding `accounts` must work on a file that already holds accounts and their tokens.

    SQLite swaps a table by dropping it, which fails with foreign keys on once another table
    points at a row, so the upgrade is run on a file that holds rows, not an empty one.
    """
    db = tmp_path / "slate.db"
    old = "fbf0dc707d9f"  # the last revision before accounts started being rebuilt
    up = _alembic(db, "upgrade", old)
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


def test_agent_run_migration_marks_17_00_chicago_due_dates_date_only(tmp_path):
    """Before `items.due_date_only`, a date-only due date was stored as 17:00 Chicago. The
    migration that adds the flag marks exactly those (CST and CDT), and nothing else."""
    db = tmp_path / "slate.db"
    up = _alembic(db, "upgrade", "5b7d2e9a4c18")
    assert up.returncode == 0, up.stderr
    rows = {
        "it_cst": "2026-11-03T23:00:00.000000Z",  # 17:00 CST
        "it_cdt": "2026-07-01T22:00:00.000000Z",  # 17:00 CDT
        "it_noon": "2026-11-03T18:00:00.000000Z",  # 12:00 CST
        "it_none": None,
    }
    with sqlite3.connect(db) as conn:
        _row(conn, "accounts", id="acc000000001", handle="alex", kind="person", role="owner")
        _row(conn, "sections", id="sec000000001", audience="personal")
        _row(conn, "lists", id="lst000000001", section_id="sec000000001")
        for item_id, due in rows.items():
            _row(
                conn,
                "items",
                id=item_id,
                due_at=due,
                kind="task",
                list_id="lst000000001",
                created_by="acc000000001",
            )

    head = _alembic(db, "upgrade", "head")
    assert head.returncode == 0, head.stderr
    with sqlite3.connect(db) as conn:
        got = dict(conn.execute("SELECT id, due_date_only FROM items"))
    assert got == {"it_cst": 1, "it_cdt": 1, "it_noon": 0, "it_none": 0}


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


def test_unique_external_ref_migration_renames_older_duplicates(tmp_path):
    """The newest holder of a duplicated reference keeps it; older ones become `#dupN`."""
    db = tmp_path / "slate.db"
    up = _alembic(db, "upgrade", "9d4f7b2c6e18")
    assert up.returncode == 0, up.stderr
    planted = {
        "it_new": ("clickup:1", "2026-10-03T00:00:00.000000Z"),
        "it_mid": ("clickup:1", "2026-10-02T00:00:00.000000Z"),
        "it_old": ("clickup:1", "2026-10-01T00:00:00.000000Z"),
        "it_solo": ("clickup:2", "2026-10-01T00:00:00.000000Z"),
        "it_none": (None, "2026-10-01T00:00:00.000000Z"),
        "it_none2": (None, "2026-10-01T00:00:00.000000Z"),
    }
    with sqlite3.connect(db) as conn:
        _row(conn, "accounts", id="acc000000001", handle="alex", kind="person", role="owner")
        _row(conn, "sections", id="sec000000001", audience="personal")
        _row(conn, "lists", id="lst000000001", section_id="sec000000001")
        for item_id, (ref, created) in planted.items():
            _row(
                conn,
                "items",
                id=item_id,
                external_ref=ref,
                created_at=created,
                kind="task",
                list_id="lst000000001",
                created_by="acc000000001",
            )

    head = _alembic(db, "upgrade", "head")
    assert head.returncode == 0, head.stderr
    assert "renamed 2 duplicate row(s)" in head.stdout + head.stderr
    with sqlite3.connect(db) as conn:
        got = dict(conn.execute("SELECT id, external_ref FROM items"))
        assert got == {
            "it_new": "clickup:1",
            "it_mid": "clickup:1#dup1",
            "it_old": "clickup:1#dup2",
            "it_solo": "clickup:2",
            "it_none": None,
            "it_none2": None,
        }
        try:
            _row(
                conn,
                "items",
                id="it_x",
                external_ref="clickup:2",
                kind="task",
                list_id="lst000000001",
                created_by="acc000000001",
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("the unique index did not stop a duplicate")
