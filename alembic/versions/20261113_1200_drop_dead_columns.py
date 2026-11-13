"""Drop columns nothing reads, and the `doc` item kind

- `comments.anchor_version_id`, `anchor_start`, `anchor_end`, `anchor_quote`: never written;
  doc review threads live in `doc_comments`.
- `attachments.path`: always `db:<id>`; the bytes are in `attachment_blobs`.
- `work_leases.from_pool`: written on a take, never read for anything.
- `items.kind = 'doc'`: docs are their own tables, and nothing creates such an item. Any that
  exist become tasks, and the CHECK on `items.kind` allows `task` only.

Downgrade puts the columns back (`attachments.path` refilled as `db:<id>`, `from_pool` false)
and widens the CHECK again; items that were `doc` stay tasks.

Revision ID: 498d15b42dd4
Revises: b4f8d2e6a1c9
Create Date: 2026-11-13 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "498d15b42dd4"
down_revision = "b4f8d2e6a1c9"
branch_labels = None
depends_on = None

WIDE = "CONSTRAINT item_kind CHECK (kind IN ('task', 'doc'))"
NARROW = "CONSTRAINT item_kind CHECK (kind IN ('task'))"


def _rebuild_items(old: str, new: str) -> None:
    """Swap the CHECK on `items.kind` by SQLite's own procedure: a new table from the stored
    statement with one clause changed, the rows copied, the old table dropped, the new one
    renamed, then its indexes and triggers created again from their stored text. (`env.py`
    turns foreign keys off around migrations and checks them after.)"""
    conn = op.get_bind()
    table_sql = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'items'"
    ).scalar_one()
    assert table_sql.count(old) == 1, "items.kind CHECK is not the expected one"
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
        batch.drop_column("anchor_version_id")
        batch.drop_column("anchor_start")
        batch.drop_column("anchor_end")
        batch.drop_column("anchor_quote")
    with op.batch_alter_table("attachments") as batch:
        batch.drop_column("path")
    with op.batch_alter_table("work_leases") as batch:
        batch.drop_column("from_pool")
    op.execute("UPDATE items SET kind = 'task' WHERE kind = 'doc'")
    _rebuild_items(WIDE, NARROW)


def downgrade() -> None:
    _rebuild_items(NARROW, WIDE)
    with op.batch_alter_table("work_leases") as batch:
        batch.add_column(sa.Column("from_pool", sa.Boolean(), nullable=False, server_default="0"))
    with op.batch_alter_table("attachments") as batch:
        batch.add_column(
            sa.Column("path", sa.String(length=1000), nullable=False, server_default="")
        )
    op.execute("UPDATE attachments SET path = 'db:' || id")
    with op.batch_alter_table("comments") as batch:
        batch.add_column(
            sa.Column(
                "anchor_version_id",
                sa.String(length=12),
                sa.ForeignKey("body_versions.id", name="fk_comments_anchor_version_id"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("anchor_start", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("anchor_end", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("anchor_quote", sa.Text(), nullable=True))
