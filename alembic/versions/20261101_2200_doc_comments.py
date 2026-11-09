"""doc_comments: review threads and discussion on pages and files

One table for both surfaces: a review thread is anchored to a span, a page or a spot and can
be resolved; discussion is the unanchored conversation. Every comment is stamped with the
revision it was written on.

Revision ID: a7c3e9f1b5d2
Revises: f4b8d2a6c1e9
Create Date: 2026-11-01 22:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from slate.models import UTCDateTime

revision = "a7c3e9f1b5d2"
down_revision = "f4b8d2a6c1e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_comments",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column(
            "doc_id",
            sa.String(length=12),
            sa.ForeignKey("docs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(length=12),
            sa.ForeignKey("doc_comments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("surface", sa.String(length=10), nullable=False),
        sa.Column("author_id", sa.String(length=12), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("edited_at", UTCDateTime(), nullable=True),
        sa.Column("anchor_revision", sa.Integer(), nullable=False),
        sa.Column("anchor_start", sa.Integer(), nullable=True),
        sa.Column("anchor_end", sa.Integer(), nullable=True),
        sa.Column("anchor_quote", sa.Text(), nullable=True),
        sa.Column("anchor_page", sa.Integer(), nullable=True),
        sa.Column("anchor_x", sa.Float(), nullable=True),
        sa.Column("anchor_y", sa.Float(), nullable=True),
        sa.Column("suggestion_md", sa.Text(), nullable=True),
        sa.Column("accepted_at", UTCDateTime(), nullable=True),
        sa.Column("assignee_id", sa.String(length=12), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("resolved_at", UTCDateTime(), nullable=True),
        sa.Column(
            "resolved_by_id", sa.String(length=12), sa.ForeignKey("accounts.id"), nullable=True
        ),
        sa.CheckConstraint("surface IN ('review', 'discussion')", name="ck_doc_comments_surface"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("doc_comments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_doc_comments_doc_id"), ["doc_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_doc_comments_parent_id"), ["parent_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_doc_comments_assignee_id"), ["assignee_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("doc_comments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_doc_comments_assignee_id"))
        batch_op.drop_index(batch_op.f("ix_doc_comments_parent_id"))
        batch_op.drop_index(batch_op.f("ix_doc_comments_doc_id"))
    op.drop_table("doc_comments")
