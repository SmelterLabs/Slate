"""drop Slack and Discord channels: notify_channels

Drops the table of the removed Slack and Discord channels feature (incoming-webhook URLs
Slate posted task changes to, scoped to a list, a section or every list). The channels go,
with the engine's `engine_cursors` row; generic webhooks (`webhooks`, `deliveries`) and push
notifications stay.

Revision ID: b8d4f2a6c9e1
Revises: a7c3e9d1f5b2
Create Date: 2026-11-01 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8d4f2a6c9e1"
down_revision = "a7c3e9d1f5b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No other table gained a column for channels (automations kept a channel id inside their
    # actions JSON, and the `notify` action is gone with the feature), so only the table and
    # the engine's cursor row go.
    with op.batch_alter_table("notify_channels", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notify_channels_section_id"))
        batch_op.drop_index(batch_op.f("ix_notify_channels_list_id"))
    op.drop_table("notify_channels")
    op.execute(sa.text("DELETE FROM engine_cursors WHERE name = 'notify_channels'"))


def downgrade() -> None:
    # The feature was removed; the empty table comes back only so `alembic downgrade base`
    # (which tests/test_migrations.py runs) keeps working. Its rows are gone for good.
    op.create_table(
        "notify_channels",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("url_hint", sa.String(length=80), nullable=False),
        sa.Column("link_base", sa.String(length=200), nullable=True),
        sa.Column("list_id", sa.String(length=12), nullable=True),
        sa.Column("section_id", sa.String(length=12), nullable=True),
        sa.Column("events_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.Column("last_ok_at", sa.String(length=27), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("notify_channels", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notify_channels_list_id"), ["list_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_notify_channels_section_id"), ["section_id"], unique=False
        )
