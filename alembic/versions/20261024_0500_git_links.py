"""GitHub pull requests, issues and commits linked to items

Creates `git_links`. The GitHub connection itself was a row in `integrations` (kind
`github`). The feature was removed; a later migration drops both tables.

Revision ID: 6b2f9c41e7d3
Revises: c3d8f1a6b5e2
Create Date: 2026-10-24 05:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6b2f9c41e7d3"
down_revision = "c3d8f1a6b5e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "git_links",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("repo", sa.String(length=200), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("sha", sa.String(length=40), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=True),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.Column("synced_at", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "url"),
    )
    op.create_index(op.f("ix_git_links_item_id"), "git_links", ["item_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_git_links_item_id"), table_name="git_links")
    op.drop_table("git_links")
