"""Add list notes, filing defaults and reference lists."""

import sqlalchemy as sa
from alembic import op

revision = "e421c56a8d37"
down_revision = "7c3a9e5f1b28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lists", sa.Column("note_md", sa.Text()))
    op.execute(
        "ALTER TABLE lists ADD COLUMN default_assignee_id VARCHAR(12) REFERENCES accounts(id)"
    )
    op.add_column(
        "lists", sa.Column("default_tags", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column("lists", sa.Column("reference", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    for name in ("reference", "default_tags", "default_assignee_id", "note_md"):
        op.drop_column("lists", name)
