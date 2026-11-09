"""doc_files.text: extracted text per revision, for search and PDF/spreadsheet compare

Existing revisions have no text until they are re-uploaded or `slate backfill-doc-text`
fills the column.

Revision ID: b8d4f2a7c3e1
Revises: a7c3e9f1b5d2
Create Date: 2026-11-01 23:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8d4f2a7c3e1"
down_revision = "a7c3e9f1b5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("doc_files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("text", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("doc_files", schema=None) as batch_op:
        batch_op.drop_column("text")
