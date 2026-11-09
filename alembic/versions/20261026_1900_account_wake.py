"""Agent wake modes: how an agent is woken for a task (push, pull, manual)

`accounts.wake`, default push. service/accounts.py WAKE_MODES.

Revision ID: 6b2d8e4f1a73
Revises: 3e6a9d2b7f41
Create Date: 2026-10-26 19:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6b2d8e4f1a73"
down_revision = "3e6a9d2b7f41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("wake", sa.String(length=8), nullable=False, server_default="push"),
    )


def downgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_column("wake")
