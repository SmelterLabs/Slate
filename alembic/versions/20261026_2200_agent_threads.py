"""Card conversation: each agent's own thread for an item (opaque ref and optional url)

`agent_threads` (one per item and agent). service/reports.py.

Revision ID: 9d4f7b2c6e18
Revises: 6b2d8e4f1a73
Create Date: 2026-10-26 22:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9d4f7b2c6e18"
down_revision = "6b2d8e4f1a73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_threads",
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("ref", sa.String(length=200), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=True),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id", "account_id"),
    )


def downgrade() -> None:
    op.drop_table("agent_threads")
