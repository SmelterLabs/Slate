"""Email notifications: `email_deliveries`, events waiting to be emailed if still unseen

Revision ID: a7e2c9d4f1b3
Revises: f1c4d7a2b8e6
Create Date: 2026-11-05 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7e2c9d4f1b3"
down_revision = "f1c4d7a2b8e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_deliveries",
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("event_id", sa.String(length=12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("send_after", sa.String(length=27), nullable=True),
        sa.Column("expires_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
        sa.PrimaryKeyConstraint("account_id", "event_id"),
    )
    op.create_index("ix_email_deliveries_send_after", "email_deliveries", ["send_after"])
    op.create_index("ix_email_deliveries_expires_at", "email_deliveries", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_email_deliveries_expires_at", table_name="email_deliveries")
    op.drop_index("ix_email_deliveries_send_after", table_name="email_deliveries")
    op.drop_table("email_deliveries")
