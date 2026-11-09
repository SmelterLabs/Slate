"""drawers: lists.kind, docs.kind folder, pinned revisions

A drawer is a list whose entries are documents. Existing lists are `tasks`. A doc may be a
`folder` inside a drawer. A file doc can pin one revision.

Revision ID: d2f6a8c4e1b7
Revises: e1f7a3c9b2d4
Create Date: 2026-11-02 01:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d2f6a8c4e1b7"
down_revision = "e1f7a3c9b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lists", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=8), server_default="tasks", nullable=False)
        )
    with op.batch_alter_table("docs", schema=None) as batch_op:
        batch_op.drop_constraint("ck_docs_kind", type_="check")
        batch_op.create_check_constraint("ck_docs_kind", "kind IN ('page', 'file', 'folder')")
        batch_op.add_column(sa.Column("pinned_revision_no", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("pinned_note", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("docs", schema=None) as batch_op:
        batch_op.drop_column("pinned_note")
        batch_op.drop_column("pinned_revision_no")
        batch_op.drop_constraint("ck_docs_kind", type_="check")
        batch_op.create_check_constraint("ck_docs_kind", "kind IN ('page', 'file')")
    with op.batch_alter_table("lists", schema=None) as batch_op:
        batch_op.drop_column("kind")
