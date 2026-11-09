"""Fence execution attempts; legacy leases stay unproven until drained/reconciled.

Revision ID: a154f3c901de
Revises: c4e8a2b6d901
"""

import sqlalchemy as sa
from alembic import op

revision = "a154f3c901de"
down_revision = "c4e8a2b6d901"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("work_leases", sa.Column("run_id", sa.String(64), nullable=True))
    op.add_column(
        "work_leases", sa.Column("asked_only", sa.Boolean(), nullable=False, server_default="0")
    )
    op.add_column(
        "work_leases", sa.Column("quarantined", sa.Boolean(), nullable=False, server_default="0")
    )
    op.add_column("chat_wakes", sa.Column("run_id", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("chat_wakes", "run_id")
    op.drop_column("work_leases", "asked_only")
    op.drop_column("work_leases", "quarantined")
    op.drop_column("work_leases", "run_id")
