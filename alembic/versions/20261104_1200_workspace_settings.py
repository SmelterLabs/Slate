"""Workspace settings (key -> JSON): the agent action deny-list.

Revision ID: d9a4f2c7e1b5
Revises: b7c2d9e4f1a3
Create Date: 2026-11-04 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from slate.models import UTCDateTime

revision = "d9a4f2c7e1b5"
down_revision = "b7c2d9e4f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("workspace_settings")
