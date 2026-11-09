"""Email to task: per-list inbound addresses and the messages they turned into tasks

Creates `mail_inboxes` (one address per list) and `mail_messages` (Message-ID -> task, for
replies and repeated deliveries). The feature was removed; a later migration drops the tables.

Revision ID: 8e4c2a7d1f53
Revises: f1c6d8e2a4b7
Create Date: 2026-10-24 08:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8e4c2a7d1f53"
down_revision = "f1c6d8e2a4b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_inboxes",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("list_id", sa.String(length=12), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("list_id"),
        sa.UniqueConstraint("token"),
    )
    op.create_table(
        "mail_messages",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("message_id", sa.String(length=300), nullable=False),
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("comment_id", sa.String(length=12), nullable=True),
        sa.Column("sender", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(op.f("ix_mail_messages_item_id"), "mail_messages", ["item_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_messages_item_id"), table_name="mail_messages")
    op.drop_table("mail_messages")
    op.drop_table("mail_inboxes")
