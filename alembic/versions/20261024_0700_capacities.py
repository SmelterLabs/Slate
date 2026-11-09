"""capacities: how much each account can take on

Revision ID: f1c6d8e2a4b7
Revises: e4b7a2c9d1f3
Create Date: 2026-10-24 07:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1c6d8e2a4b7"
down_revision = "e4b7a2c9d1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capacities",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("hours_per_day", sa.Float(), nullable=True),
        sa.Column("points_per_week", sa.Float(), nullable=True),
        sa.Column("tasks_per_day", sa.Integer(), nullable=True),
        sa.Column("work_days", sa.String(length=13), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id"),
    )


def downgrade() -> None:
    op.drop_table("capacities")
