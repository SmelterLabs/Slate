"""Comment dots: `item_reads`, when each account last opened each item

A comment written after it is new to that account (service/item_reads.py). Every item that
already has comments starts read for every existing account, so the board does not light up
with history after the upgrade; only comments written afterwards make a dot.

Revision ID: f1c4d7a2b8e6
Revises: e3b7c1a9d5f2
Create Date: 2026-11-04 20:00:00
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "f1c4d7a2b8e6"
down_revision = "e3b7c1a9d5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_reads",
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("read_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "item_id"),
    )
    op.create_index("ix_item_reads_item_id", "item_reads", ["item_id"])
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # models.UTCDateTime
    op.get_bind().execute(
        sa.text(
            "INSERT INTO item_reads (account_id, item_id, read_at)"
            " SELECT a.id, c.item_id, :now FROM accounts a"
            " JOIN (SELECT DISTINCT item_id FROM comments) c"
        ),
        {"now": now},
    )


def downgrade() -> None:
    op.drop_index("ix_item_reads_item_id", table_name="item_reads")
    op.drop_table("item_reads")
