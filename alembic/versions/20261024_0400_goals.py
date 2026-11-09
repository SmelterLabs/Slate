"""goals and their targets

Creates `goals` and `goal_targets`: a goal had targets of four kinds (number, currency,
true/false, tasks). The feature was removed; a later migration drops the tables.

Revision ID: a7c41e9b2d60
Revises: 5e0b7c2a9d41
Create Date: 2026-10-24 04:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c41e9b2d60"
down_revision = "5e0b7c2a9d41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description_md", sa.Text(), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("owner_id", sa.String(length=12), nullable=True),
        sa.Column("due_at", sa.String(length=27), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.String(length=27), nullable=True),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "goal_targets",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("goal_id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "number",
                "currency",
                "boolean",
                "tasks",
                name="goal_target_kind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("start_value", sa.Float(), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=True),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("list_id", sa.String(length=12), nullable=True),
        sa.Column("item_ids_json", sa.JSON(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_goal_targets_goal_id", "goal_targets", ["goal_id"])


def downgrade() -> None:
    op.drop_index("ix_goal_targets_goal_id", table_name="goal_targets")
    op.drop_table("goal_targets")
    op.drop_table("goals")
