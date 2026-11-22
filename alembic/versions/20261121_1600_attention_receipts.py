"""Store shared pending and released alerts and terminal push failure times.

Revision ID: 63af8de1487b
Revises: a3e7f1c8b264
"""

import sqlalchemy as sa
from alembic import op

revision = "63af8de1487b"
down_revision = "a3e7f1c8b264"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("push_deliveries") as batch:
        batch.add_column(sa.Column("failed_at", sa.String(27)))
    op.create_table(
        "attention_alerts",
        sa.Column("account_id", sa.String(12), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("event_id", sa.String(12), sa.ForeignKey("events.id"), primary_key=True),
        sa.Column("source_at", sa.String(27), nullable=False),
        sa.Column("cursor", sa.String(12)),
        sa.Column("at", sa.String(27)),
        sa.Column("reason", sa.String(20)),
        sa.Column("loudness", sa.Integer()),
        sa.Column("quiet_events", sa.JSON()),
    )
    op.create_index("ix_attention_alerts_source_at", "attention_alerts", ["source_at"])
    op.create_index(
        "ix_attention_alerts_account_cursor", "attention_alerts", ["account_id", "cursor"]
    )
    op.execute(
        "INSERT INTO attention_alerts (account_id, event_id, source_at) "
        "SELECT account_id, event_id, events.at FROM push_deliveries "
        "JOIN events ON events.id = push_deliveries.event_id "
        "WHERE state = 'pending' AND kind = 'notification' "
        "UNION SELECT account_id, event_id, events.at FROM email_deliveries "
        "JOIN events ON events.id = email_deliveries.event_id WHERE state = 'pending'"
    )


def downgrade() -> None:
    op.drop_table("attention_alerts")
    with op.batch_alter_table("push_deliveries") as batch:
        batch.drop_column("failed_at")
