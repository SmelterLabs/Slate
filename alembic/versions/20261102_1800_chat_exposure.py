"""Remember exact exposed chat messages without clearing skipped replies.

Revision ID: c4e8a2b6d901
Revises: a149dc02b001
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e8a2b6d901"
down_revision = "a149dc02b001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "channel_reads", sa.Column("exposed_ids", sa.JSON(), nullable=False, server_default="[]")
    )


def downgrade():
    op.drop_column("channel_reads", "exposed_ids")
