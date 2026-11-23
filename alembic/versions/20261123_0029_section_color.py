"""section color

Revision ID: 48ca17da99a9
Revises: 6482fd0ea149
Create Date: 2026-11-23 00:29:47.304962
"""

import sqlalchemy as sa
from alembic import op

revision = "48ca17da99a9"
down_revision = "6482fd0ea149"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sections", sa.Column("color", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sections") as batch_op:
        batch_op.drop_column("color")
