"""sprints: sprint folders, sprints, and sprint points on items

Creates `sprint_folders` and `sprints` and adds `items.sprint_id` and `items.points`. The
feature was removed; a later migration drops them.

Revision ID: e4b7a2c9d1f3
Revises: 3d8a5e17c2b9
Create Date: 2026-10-24 06:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4b7a2c9d1f3"
down_revision = "3d8a5e17c2b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sprint_folders",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("section_id", sa.String(length=12), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("start_weekday", sa.Integer(), nullable=False),
        sa.Column("auto_rollover", sa.Boolean(), nullable=False),
        sa.Column("auto_create", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.String(length=27), nullable=True),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sprints",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("folder_id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.String(length=10), nullable=False),
        sa.Column("end_date", sa.String(length=10), nullable=False),
        sa.Column("goal_md", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.String(length=27), nullable=True),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["sprint_folders.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sprints_folder_id", "sprints", ["folder_id"])
    # Raw ADD COLUMN, not batch mode: batch would rebuild `items` and drop its depth triggers
    # (see 7a9e4c1d2b30). SQLite accepts an inline REFERENCES when the default is NULL.
    op.add_column("items", sa.Column("points", sa.Float(), nullable=True))
    op.execute("ALTER TABLE items ADD COLUMN sprint_id VARCHAR(12) REFERENCES sprints (id)")
    op.create_index(op.f("ix_items_sprint_id"), "items", ["sprint_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_items_sprint_id"))
        batch_op.drop_column("sprint_id")
        batch_op.drop_column("points")
    op.drop_index("ix_sprints_folder_id", table_name="sprints")
    op.drop_table("sprints")
    op.drop_table("sprint_folders")
