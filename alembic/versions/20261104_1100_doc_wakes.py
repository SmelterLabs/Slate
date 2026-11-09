"""Doc mentions wake agents: an agent's pending or open turn on a page or a doc thread

`doc_wakes` (one per agent, doc and thread: the newest mention, how many mentions came and how
many a take covers, and the lease). service/docwake.py and docs/agents-work-loop.md.

Revision ID: d8e4a1c7f3b9
Revises: b7c2d9e4f1a3
Create Date: 2026-11-04 11:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from slate.models import UTCDateTime

revision = "d8e4a1c7f3b9"
down_revision = "d9a4f2c7e1b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_wakes",
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column("doc_id", sa.String(length=12), nullable=False),
        sa.Column("thread_id", sa.String(length=12), nullable=False),
        sa.Column("comment_id", sa.String(length=12), nullable=True),
        sa.Column("by_id", sa.String(length=12), nullable=False),
        sa.Column("excerpt", sa.String(length=200), nullable=False),
        sa.Column("mention_no", sa.Integer(), nullable=False),
        sa.Column("taken_no", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("claimed_at", UTCDateTime(), nullable=True),
        sa.Column("until", UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["doc_id"], ["docs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "doc_id", "thread_id"),
    )
    op.create_index(op.f("ix_doc_wakes_until"), "doc_wakes", ["until"])


def downgrade() -> None:
    op.drop_index(op.f("ix_doc_wakes_until"), table_name="doc_wakes")
    op.drop_table("doc_wakes")
