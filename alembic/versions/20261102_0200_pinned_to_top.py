"""docs pinned to the top of a drawer or folder

A document in a drawer (a file, a page or a folder) can be pinned so it sits first in its
listing (the drawer's top or its folder), with a pin glyph. Shared, not per account: the
drawer is the project's shelf.

Revision ID: f3a9c7e2b5d1
Revises: d2f6a8c4e1b7
Create Date: 2026-11-02 02:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import slate.models

revision = "f3a9c7e2b5d1"
down_revision = "d2f6a8c4e1b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("docs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("top_pinned_at", slate.models.UTCDateTime(length=27), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "top_pinned_by",
                sa.String(length=12),
                sa.ForeignKey("accounts.id", name="fk_docs_top_pinned_by_accounts"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("docs", schema=None) as batch_op:
        batch_op.drop_constraint("fk_docs_top_pinned_by_accounts", type_="foreignkey")
        batch_op.drop_column("top_pinned_by")
        batch_op.drop_column("top_pinned_at")
