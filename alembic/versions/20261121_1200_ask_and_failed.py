"""An agent's question on a comment, and the `failed` agent badge

- `comments.ask_json`: the question and its options when the comment is an agent's `ask`
  (`{"question": str, "options": [str]}`), else null.
- `items.agent_state` may be `failed` (the run broke; no person is asked anything).

Downgrade drops the column and narrows the CHECK again; a `failed` badge becomes `blocked`.

Revision ID: 5d2e8a1f9c47
Revises: 3b9f0c2a7d51
Create Date: 2026-11-21 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5d2e8a1f9c47"
down_revision = "3b9f0c2a7d51"
branch_labels = None
depends_on = None

NARROW = "CONSTRAINT agent_state CHECK (agent_state IN ('working', 'blocked', 'review'))"
WIDE = "CONSTRAINT agent_state CHECK (agent_state IN ('working', 'blocked', 'review', 'failed'))"


def _rebuild_items(old: str, new: str) -> None:
    """Swap the CHECK on `items.agent_state` by SQLite's own procedure: a new table from the
    stored statement with one clause changed, the rows copied, the old table dropped, the new
    one renamed, then its indexes and triggers created again from their stored text. (`env.py`
    turns foreign keys off around migrations and checks them after.)"""
    conn = op.get_bind()
    table_sql = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'items'"
    ).scalar_one()
    assert table_sql.count(old) == 1, "items.agent_state CHECK is not the expected one"
    dependents = [
        row[0]
        for row in conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE tbl_name = 'items' "
            "AND type IN ('index', 'trigger') AND sql IS NOT NULL ORDER BY rowid"
        )
    ]
    head, rest = table_sql.split("(", 1)
    assert head.strip() in ('CREATE TABLE "items"', "CREATE TABLE items"), head
    conn.exec_driver_sql('CREATE TABLE "_items_new" (' + rest.replace(old, new))
    conn.exec_driver_sql('INSERT INTO "_items_new" SELECT * FROM items')
    conn.exec_driver_sql("DROP TABLE items")
    conn.exec_driver_sql('ALTER TABLE "_items_new" RENAME TO items')
    for statement in dependents:
        conn.exec_driver_sql(statement)


def upgrade() -> None:
    with op.batch_alter_table("comments") as batch:
        batch.add_column(sa.Column("ask_json", sa.JSON(), nullable=True))
    _rebuild_items(NARROW, WIDE)


def downgrade() -> None:
    op.execute("UPDATE items SET agent_state = 'blocked' WHERE agent_state = 'failed'")
    _rebuild_items(WIDE, NARROW)
    with op.batch_alter_table("comments") as batch:
        batch.drop_column("ask_json")
