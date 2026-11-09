"""drop sprints: sprint_folders, sprints, items.sprint_id, items.points, capacities.points_per_week

Drops the tables and columns of the removed sprints feature (sprint folders, sprints, sprint
points, rollover, burndown and velocity). The tasks that were planned into sprints stay; their
`sprint_id` and `points` go, as does the capacity in points a week (`points_per_week`), which
measured nothing else.

Revision ID: d8f2b4a6c1e7
Revises: c7e3a1f5b2d9
Create Date: 2026-11-01 15:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8f2b4a6c1e7"
down_revision = "c7e3a1f5b2d9"
branch_labels = None
depends_on = None

# Batch mode rebuilds `items` (SQLite cannot drop a column that a foreign key names) and the
# rebuild drops the table's triggers, so they are made again below. These repeat
# slate.models.TRIGGERS["items"] verbatim, like the initial schema does.
ITEM_TRIGGERS = [
    """CREATE TRIGGER items_depth_insert BEFORE INSERT ON items
WHEN NEW.parent_id IS NOT NULL AND (
  (SELECT parent_id FROM items WHERE id = NEW.parent_id) IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'item depth exceeds 1: parent is itself a subtask'); END""",
    """CREATE TRIGGER items_depth_update BEFORE UPDATE OF parent_id ON items
WHEN NEW.parent_id IS NOT NULL AND (
  NEW.parent_id = NEW.id
  OR (SELECT parent_id FROM items WHERE id = NEW.parent_id) IS NOT NULL
  OR EXISTS (SELECT 1 FROM items WHERE parent_id = NEW.id))
BEGIN SELECT RAISE(ABORT, 'item depth exceeds 1: parent is a subtask or item has subtasks');
END""",
]


def upgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_items_sprint_id"))
        batch_op.drop_column("sprint_id")
        batch_op.drop_column("points")
    for sql in ITEM_TRIGGERS:
        op.execute(sql)
    with op.batch_alter_table("capacities", schema=None) as batch_op:
        batch_op.drop_column("points_per_week")
    op.drop_index("ix_sprints_folder_id", table_name="sprints")
    op.drop_table("sprints")
    op.drop_table("sprint_folders")


def downgrade() -> None:
    # Sprints were removed; the empty tables and columns come back only so `alembic downgrade
    # base` (which tests/test_migrations.py runs) keeps working. Their rows are gone for good.
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
    # Raw ADD COLUMN, not batch mode, so `items` is not rebuilt (that would drop its triggers).
    op.add_column("capacities", sa.Column("points_per_week", sa.Float(), nullable=True))
    op.add_column("items", sa.Column("points", sa.Float(), nullable=True))
    op.execute("ALTER TABLE items ADD COLUMN sprint_id VARCHAR(12) REFERENCES sprints (id)")
    op.create_index(op.f("ix_items_sprint_id"), "items", ["sprint_id"], unique=False)
