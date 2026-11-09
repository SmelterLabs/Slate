"""webhook secret_enc and delivery queue

Revision ID: 6493f941e266
Revises: db8e37ea0496
Create Date: 2026-10-23 02:46:43.863197

Webhook secrets are encrypted at rest (slate.crypto) instead of hashed, so Slate can sign
deliveries. Deliveries gain the durable-queue columns. A webhook row that predates this
revision gets an empty `secret_enc` and fails to deliver with a clear error until it is
re-created.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6493f941e266"
down_revision = "db8e37ea0496"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("deliveries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("state", sa.String(length=10), nullable=False, server_default="ok")
        )
        batch_op.add_column(sa.Column("next_at", sa.String(length=27), nullable=True))
        batch_op.add_column(sa.Column("body", sa.Text(), nullable=False, server_default=""))
        batch_op.create_index(batch_op.f("ix_deliveries_next_at"), ["next_at"], unique=False)

    with op.batch_alter_table("webhooks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("secret_enc", sa.Text(), nullable=False, server_default=""))
        batch_op.drop_column("secret_hash")


def downgrade() -> None:
    with op.batch_alter_table("webhooks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("secret_hash", sa.VARCHAR(length=128), nullable=False, server_default="")
        )
        batch_op.drop_column("secret_enc")

    with op.batch_alter_table("deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deliveries_next_at"))
        batch_op.drop_column("body")
        batch_op.drop_column("next_at")
        batch_op.drop_column("state")
