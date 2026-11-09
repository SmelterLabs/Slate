"""index items.waiting_on_account_id (check-in queue and Today read it per account)

Revision ID: dba170faeb77
Revises: 38b0917911cc
Create Date: 2026-10-24 02:34:46.732353
"""

from __future__ import annotations

from alembic import op

revision = "dba170faeb77"
down_revision = "38b0917911cc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_items_waiting_on_account_id"), ["waiting_on_account_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_items_waiting_on_account_id"))
