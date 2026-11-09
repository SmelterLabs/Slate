"""Keep page comment identity independent of folded history versions."""

import sqlalchemy as sa
from alembic import op

revision = "a149dc02b001"
down_revision = "0f6da629237e"
branch_labels = None
depends_on = None


def upgrade():
    # Existing folded history cannot establish immutable identity; leave unknowns null.
    op.add_column("doc_comments", sa.Column("anchor_page_revision", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("doc_comments", "anchor_page_revision")
