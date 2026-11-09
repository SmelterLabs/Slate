"""Blocked state: items.blocked_since

Set while an item waits on an open blocker, so the change that clears it can record
`item.unblocked` (service/links.py `settle`). Items blocked today start blocked from now.

Revision ID: b7d3e91c4a20
Revises: 8e4c2a7d1f53
Create Date: 2026-10-26 03:00:00
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "b7d3e91c4a20"
down_revision = "8e4c2a7d1f53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("blocked_since", sa.String(length=27), nullable=True))
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    op.execute(
        sa.text(
            """
            UPDATE items SET blocked_since = :now WHERE id IN (
                SELECT l.to_item_id FROM item_links l
                JOIN items b ON b.id = l.from_item_id
                LEFT JOIN statuses s ON s.id = b.status_id
                WHERE l.kind = 'blocks' AND b.archived_at IS NULL
                  AND (s.kind IS NULL OR s.kind NOT IN ('done', 'cancelled'))
            )
            """
        ).bindparams(now=now)
    )


def downgrade() -> None:
    with op.batch_alter_table("items") as batch:
        batch.drop_column("blocked_since")
