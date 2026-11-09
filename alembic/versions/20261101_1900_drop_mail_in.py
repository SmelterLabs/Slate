"""drop email to task: mail_inboxes, mail_messages

Drops the tables of the removed email-to-task feature (a list's inbound email address, read
over IMAP or posted by a mail service's inbound webhook). The per-list addresses and the
Message-ID log go, with the `integrations` row of kind `email` (its encrypted path secret and
IMAP password). GitHub links and the GitHub connection are left for a later migration.

Revision ID: c9e5a1b7d3f2
Revises: b8d4f2a6c9e1
Create Date: 2026-11-01 19:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e5a1b7d3f2"
down_revision = "b8d4f2a6c9e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No other table gained a column for mail (a mail's task and comment are ordinary rows
    # whose events carry `session_ref` email:...), so only the two tables and the connection
    # row go.
    with op.batch_alter_table("mail_messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_mail_messages_item_id"))
    op.drop_table("mail_messages")
    op.drop_table("mail_inboxes")
    op.execute(sa.text("DELETE FROM integrations WHERE kind = 'email'"))


def downgrade() -> None:
    # The feature was removed; the empty tables come back only so `alembic downgrade base`
    # (which tests/test_migrations.py runs) keeps working. Their rows are gone for good.
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
    with op.batch_alter_table("mail_messages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_mail_messages_item_id"), ["item_id"], unique=False)
