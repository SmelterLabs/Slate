"""Drop outbound webhook subscriptions and their delivery log.

Downgrade restores the empty tables. Discarded subscriptions and attempts are not recoverable.

Revision ID: a3e7f1c8b264
Revises: d8f6b2e4c913
Create Date: 2026-11-21 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3e7f1c8b264"
down_revision = "d8f6b2e4c913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("deliveries")
    op.drop_table("webhooks")


def downgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("account_id", sa.String(12), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("events_json", sa.JSON(), nullable=False),
        sa.Column("filter_json", sa.JSON()),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("disabled_at", sa.String(27)),
        sa.Column("secret_enc", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_webhooks_account_id", "webhooks", ["account_id"])
    op.create_table(
        "deliveries",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column(
            "webhook_id",
            sa.String(12),
            sa.ForeignKey("webhooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(12), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("response_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("at", sa.String(27), nullable=False),
        sa.Column("state", sa.String(10), nullable=False, server_default="ok"),
        sa.Column("next_at", sa.String(27)),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_deliveries_event_id", "deliveries", ["event_id"])
    op.create_index("ix_deliveries_next_at", "deliveries", ["next_at"])
    op.create_index("ix_deliveries_webhook_id", "deliveries", ["webhook_id"])
