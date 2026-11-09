"""account prefs

Revision ID: 3c1d9e7a52b4
Revises: fad0cd9a634b
Create Date: 2026-10-23 21:30:00

Board prefs (theme, folded columns) move from each browser's localStorage onto the account, so
every device shows the same board.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3c1d9e7a52b4"
down_revision = "fad0cd9a634b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("prefs_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_column("prefs_json")
