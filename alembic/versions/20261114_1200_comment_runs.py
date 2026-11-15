"""Run logs kept with an agent's answer

`comment_runs` holds, per comment an agent posted as a run's answer, the lines that run
posted while it worked (tool calls, what it was thinking), so the task window can show them
after the run has ended.

Revision ID: 3b9f0c2a7d51
Revises: c7a1e9d4b2f6
Create Date: 2026-11-14 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3b9f0c2a7d51"
down_revision = "c7a1e9d4b2f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "comment_runs",
        sa.Column("comment_id", sa.String(length=12), nullable=False),
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("lines_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.String(length=27), nullable=True),
        sa.Column("finished_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("comment_id"),
    )
    op.create_index("ix_comment_runs_item_id", "comment_runs", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_comment_runs_item_id", table_name="comment_runs")
    op.drop_table("comment_runs")
