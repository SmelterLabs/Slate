"""recurrence options: trigger (schedule / on_done), skip_weekends, reopen, copy_checklists

Revision ID: 5e0b7c2a9d41
Revises: dba170faeb77
Create Date: 2026-10-24 03:30:00

Existing rules keep behaving as before: trigger `schedule`, no weekend skipping, a new item per
occurrence. `copy_checklists` starts true (instances carry the last one's checklists, unticked),
which only matters for rules whose instances have checklists.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5e0b7c2a9d41"
down_revision = "dba170faeb77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recurrences") as batch_op:
        batch_op.add_column(
            sa.Column(
                "trigger",
                sa.Enum(
                    "schedule",
                    "on_done",
                    name="recur_trigger",
                    native_enum=False,
                    create_constraint=True,
                    length=16,
                ),
                nullable=False,
                server_default="schedule",
            )
        )
        batch_op.add_column(
            sa.Column("skip_weekends", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("reopen", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("copy_checklists", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("recurrences") as batch_op:
        batch_op.drop_column("copy_checklists")
        batch_op.drop_column("reopen")
        batch_op.drop_column("skip_weekends")
        batch_op.drop_column("trigger")
