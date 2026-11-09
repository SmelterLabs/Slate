"""doc_tags: the same tags tasks use, on pages and files

Revision ID: c9e5f3b8d4a2
Revises: b8d4f2a7c3e1
Create Date: 2026-11-01 23:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e5f3b8d4a2"
down_revision = "b8d4f2a7c3e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_tags",
        sa.Column(
            "doc_id",
            sa.String(length=12),
            sa.ForeignKey("docs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            sa.String(length=12),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("doc_id", "tag_id"),
    )
    with op.batch_alter_table("doc_tags", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_doc_tags_tag_id"), ["tag_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("doc_tags", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_doc_tags_tag_id"))
    op.drop_table("doc_tags")
