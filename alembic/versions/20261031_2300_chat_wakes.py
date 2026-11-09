"""Chat mentions wake agents: an agent's pending or open turn in a channel or thread

`chat_wakes` (one per agent, channel and thread: the newest and oldest unanswered mention, the
mention a take covers, and the lease). service/chatwake.py and docs/chat.md.

Revision ID: a7c3e9f1b2d4
Revises: d41b0c8e2a19
Create Date: 2026-10-31 23:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c3e9f1b2d4"
down_revision = "d41b0c8e2a19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_wakes",
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("thread_id", sa.String(length=12), nullable=False),
        sa.Column("message_id", sa.String(length=12), nullable=False),
        sa.Column("since_id", sa.String(length=12), nullable=False),
        sa.Column("taken_id", sa.String(length=12), nullable=True),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("claimed_at", sa.String(length=27), nullable=True),
        sa.Column("until", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "channel", "thread_id"),
    )
    op.create_index(op.f("ix_chat_wakes_until"), "chat_wakes", ["until"])


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_wakes_until"), table_name="chat_wakes")
    op.drop_table("chat_wakes")
