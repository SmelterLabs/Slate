"""drop forms tables

Drops the table of the removed forms feature (public intake forms on a list). The tasks
forms made stay; only the `forms` table goes.

Revision ID: e5f2a7c9d1b3
Revises: a7c3e9f1b2d4
Create Date: 2026-11-01 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f2a7c9d1b3"
down_revision = "a7c3e9f1b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_forms_list_id", table_name="forms")
    op.drop_table("forms")


def downgrade() -> None:
    # Forms were removed; the empty table comes back only so `alembic downgrade base` (which
    # tests/test_migrations.py runs) keeps working. Its rows are gone for good.
    op.create_table(
        "forms",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("list_id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description_md", sa.Text(), nullable=False),
        sa.Column("token", sa.String(length=40), nullable=False),
        sa.Column("questions_json", sa.JSON(), nullable=False),
        sa.Column("defaults_json", sa.JSON(), nullable=False),
        sa.Column("submit_label", sa.String(length=60), nullable=False),
        sa.Column("thanks_md", sa.Text(), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_forms_list_id", "forms", ["list_id"])
