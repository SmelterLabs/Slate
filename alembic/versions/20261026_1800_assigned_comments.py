"""Assigned comments: a comment addressed to one account, open until answered or resolved

`comments.assignee_id`, `resolved_at`, `resolved_by_id`. service/comments.py.

Revision ID: 3e6a9d2b7f41
Revises: 8c1f4a7e2d90
Create Date: 2026-10-26 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3e6a9d2b7f41"
down_revision = "8c1f4a7e2d90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE comments ADD COLUMN assignee_id VARCHAR(12) REFERENCES accounts (id)")
    op.add_column("comments", sa.Column("resolved_at", sa.String(length=27), nullable=True))
    op.execute(
        "ALTER TABLE comments ADD COLUMN resolved_by_id VARCHAR(12) REFERENCES accounts (id)"
    )
    op.create_index(op.f("ix_comments_assignee_id"), "comments", ["assignee_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_comments_assignee_id"))
        batch_op.drop_column("resolved_by_id")
        batch_op.drop_column("resolved_at")
        batch_op.drop_column("assignee_id")
