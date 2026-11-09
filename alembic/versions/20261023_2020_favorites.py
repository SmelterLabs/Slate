"""favorites

Revision ID: b34cdb78b19b
Revises: fbf0dc707d9f
Create Date: 2026-10-23 20:20:39.149246

Per-account pinned lists, sections, saved views and items: the board's favorites bar.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b34cdb78b19b"
down_revision = "fbf0dc707d9f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "favorites",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("account_id", sa.String(length=12), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "list",
                "section",
                "view",
                "item",
                name="favorite_kind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.String(length=12), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "kind", "target_id"),
    )
    with op.batch_alter_table("favorites", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_favorites_account_id"), ["account_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("favorites", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_favorites_account_id"))
    op.drop_table("favorites")
