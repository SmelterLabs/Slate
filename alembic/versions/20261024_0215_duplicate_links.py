"""duplicate links: item_links.kind gains `duplicates`

Revision ID: 9d41c7e2a6b8
Revises: 0b5732291aa2
Create Date: 2026-10-24 02:15:00

The kind is a non-native enum (a VARCHAR with a CHECK constraint named `link_kind`); SQLite
cannot alter a CHECK, so the table is rebuilt with the wider constraint. Downgrade deletes
`duplicates` links first, or the narrower CHECK would refuse the copy.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9d41c7e2a6b8"
down_revision = "0b5732291aa2"
branch_labels = None
depends_on = None


def _kind(*values: str) -> sa.Enum:
    return sa.Enum(*values, name="link_kind", native_enum=False, create_constraint=True, length=16)


def upgrade() -> None:
    with op.batch_alter_table("item_links", recreate="always") as batch_op:
        batch_op.alter_column(
            "kind",
            existing_type=_kind("blocks", "relates"),
            type_=_kind("blocks", "relates", "duplicates"),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute("DELETE FROM item_links WHERE kind = 'duplicates'")
    with op.batch_alter_table("item_links", recreate="always") as batch_op:
        batch_op.alter_column(
            "kind",
            existing_type=_kind("blocks", "relates", "duplicates"),
            type_=_kind("blocks", "relates"),
            existing_nullable=False,
        )
