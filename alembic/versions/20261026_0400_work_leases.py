"""Agent work loop: leases on tasks agents took with POST /work/next

`work_leases` (one per taken task: who, until when, whether the take assigned them).
service/work.py.

Revision ID: 5b7d2e9a4c18
Revises: b7d3e91c4a20
Create Date: 2026-10-26 04:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5b7d2e9a4c18"
down_revision = "b7d3e91c4a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_leases",
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("claimed_at", sa.String(length=27), nullable=False),
        sa.Column("until", sa.String(length=27), nullable=False),
        sa.Column("from_pool", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index(op.f("ix_work_leases_account_id"), "work_leases", ["account_id"])
    op.create_index(op.f("ix_work_leases_until"), "work_leases", ["until"])


def downgrade() -> None:
    op.drop_index(op.f("ix_work_leases_until"), table_name="work_leases")
    op.drop_index(op.f("ix_work_leases_account_id"), table_name="work_leases")
    op.drop_table("work_leases")
