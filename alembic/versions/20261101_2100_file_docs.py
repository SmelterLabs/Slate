"""file docs: docs.kind and doc_files revisions

A doc is a `page` (Markdown written in Slate) or a `file` (uploaded bytes with linear
revisions), both in one tree. Existing docs are pages; existing doc files (images inside
pages) have no revision number.

Revision ID: f4b8d2a6c1e9
Revises: e3a7c5d9b2f4
Create Date: 2026-11-01 21:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from slate.models import UTCDateTime

revision = "f4b8d2a6c1e9"
down_revision = "e3a7c5d9b2f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("docs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=8), server_default="page", nullable=False)
        )
        batch_op.create_check_constraint("ck_docs_kind", "kind IN ('page', 'file')")
    with op.batch_alter_table("doc_files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revision_no", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("note", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("superseded_at", UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("restored_from", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_doc_files_doc_id_revision_no", ["doc_id", "revision_no"]
        )


def downgrade() -> None:
    with op.batch_alter_table("doc_files", schema=None) as batch_op:
        batch_op.drop_constraint("uq_doc_files_doc_id_revision_no", type_="unique")
        batch_op.drop_column("restored_from")
        batch_op.drop_column("superseded_at")
        batch_op.drop_column("note")
        batch_op.drop_column("revision_no")
    with op.batch_alter_table("docs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_docs_kind", type_="check")
        batch_op.drop_column("kind")
