"""drop task templates and status templates: task_templates, status_templates

Drops the tables of the removed task templates (saved task shapes filed from the palette or
`slate_template`) and status templates (the five built-ins plus saved column sets, applied to
a list). A new list still gets the default statuses or the ones it is given; lists made from
a template keep their columns (a template was copied, never linked).

Revision ID: c2e7a9d4b6f1
Revises: f4b8d2a6c9e3
Create Date: 2026-11-04 02:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c2e7a9d4b6f1"
down_revision = "f4b8d2a6c9e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("task_templates")
    op.drop_table("status_templates")


def downgrade() -> None:
    # The features were removed; the empty tables come back only so `alembic downgrade base`
    # (which tests/test_migrations.py runs) keeps working. Their rows are gone for good.
    op.create_table(
        "status_templates",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("statuses_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("archived_at", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "task_templates",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("list_id", sa.String(length=12), nullable=True),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.Column("archived_at", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
