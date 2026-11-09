"""forms

Creates the `forms` table: a form belonged to a list and each submission created exactly one
task there. The feature was removed; a later migration drops the table.

Revision ID: c3d8f1a6b5e2
Revises: 371ecc4b34b1
Create Date: 2026-10-24 05:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d8f1a6b5e2"
down_revision = "371ecc4b34b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_forms_list_id", table_name="forms")
    op.drop_table("forms")
