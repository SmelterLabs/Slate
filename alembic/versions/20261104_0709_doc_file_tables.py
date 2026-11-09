"""doc_files.tables_json: a CSV/TSV/XLSX revision's tables, read once on upload

Parsing both revisions' spreadsheets on every compare is slow for large workbooks. The
tables sit beside the extracted text, so the read path never opens a file. Existing revisions
compare as "not indexed" until `slate backfill-doc-text` fills the column.

Revision ID: 7c2d9e4b1f60
Revises: a160c0de2026
Create Date: 2026-11-04 07:09:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7c2d9e4b1f60"
down_revision = "a160c0de2026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("doc_files", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tables_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("doc_files", schema=None) as batch_op:
        batch_op.drop_column("tables_json")
