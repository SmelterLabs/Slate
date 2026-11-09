"""drop time tracking: time_entries

Drops the table of the removed time tracking feature (timers and logged time on items). The
items keep their `estimate_minutes` (a plan, not tracked time); the `time_entries` rows go.

Revision ID: f3a9c7e2d5b1
Revises: d8f2b4a6c1e7
Create Date: 2026-11-01 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a9c7e2d5b1"
down_revision = "d8f2b4a6c1e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No other table gained a column for time tracking (totals were summed on read), so
    # `items` is left alone and its triggers stay.
    op.drop_index("ix_time_entries_one_running", table_name="time_entries")
    op.drop_index("ix_time_entries_item_id", table_name="time_entries")
    op.drop_index("ix_time_entries_account_id_started_at", table_name="time_entries")
    op.drop_table("time_entries")


def downgrade() -> None:
    # Time tracking was removed; the empty table comes back only so `alembic downgrade base`
    # (which tests/test_migrations.py runs) keeps working. Its rows are gone for good.
    op.create_table(
        "time_entries",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("started_at", sa.String(length=27), nullable=False),
        sa.Column("ended_at", sa.String(length=27), nullable=True),
        sa.Column("seconds", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_time_entries_account_id_started_at", "time_entries", ["account_id", "started_at"]
    )
    op.create_index("ix_time_entries_item_id", "time_entries", ["item_id"])
    op.create_index(
        "ix_time_entries_one_running",
        "time_entries",
        ["account_id"],
        unique=True,
        sqlite_where=sa.text("ended_at IS NULL"),
    )
