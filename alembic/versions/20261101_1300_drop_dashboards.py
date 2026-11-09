"""drop dashboards table

Drops the table of the removed dashboards feature (named grids of report widgets, with the
seeded Overview). Nothing else referenced the table.

Revision ID: b9d4e6f2a8c1
Revises: e5f2a7c9d1b3
Create Date: 2026-11-01 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b9d4e6f2a8c1"
down_revision = "e5f2a7c9d1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("dashboards")


def downgrade() -> None:
    # Dashboards were removed; the empty table comes back only so `alembic downgrade base` (which
    # tests/test_migrations.py runs) keeps working. Its rows (the Overview included) are gone.
    op.create_table(
        "dashboards",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("widgets_json", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
