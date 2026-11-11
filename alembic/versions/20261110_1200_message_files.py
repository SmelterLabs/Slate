"""Files on chat messages: `message_files`

A file is uploaded to a channel (`message_id` null: unsent, its uploader's alone) and then
sent with a message (service/chat_files.py). The bytes stay in the database, like attachment
blobs, so the one backup covers them.

Revision ID: b4f8d2e6a1c9
Revises: a7e2c9d4f1b3
Create Date: 2026-11-10 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4f8d2e6a1c9"
down_revision = "a7e2c9d4f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_files",
        sa.Column("id", sa.String(length=12), primary_key=True),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column(
            "message_id",
            sa.String(length=12),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "uploaded_by", sa.String(length=12), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("created_at", sa.String(length=27), nullable=False),
    )
    op.create_index("ix_message_files_message_id", "message_files", ["message_id"])
    op.create_index("ix_message_files_uploaded_by", "message_files", ["uploaded_by"])


def downgrade() -> None:
    op.drop_index("ix_message_files_uploaded_by", table_name="message_files")
    op.drop_index("ix_message_files_message_id", table_name="message_files")
    op.drop_table("message_files")
