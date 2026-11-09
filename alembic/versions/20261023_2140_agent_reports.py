"""agent reports on items, per-actor event index

Revision ID: 7a9e4c1d2b30
Revises: 40ec8806a292
Create Date: 2026-10-23 21:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7a9e4c1d2b30"
down_revision = "40ec8806a292"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Raw ADD COLUMNs, not batch mode: batch would rebuild `items` and drop its depth triggers,
    # and alembic's add_column refuses an inline FK on SQLite. SQLite itself accepts an inline
    # REFERENCES / CHECK on ADD COLUMN when the default is NULL.
    op.execute(
        "ALTER TABLE items ADD COLUMN agent_state VARCHAR(16) CONSTRAINT agent_state "
        "CHECK (agent_state IN ('working', 'blocked', 'review'))"
    )
    op.execute("ALTER TABLE items ADD COLUMN agent_state_by VARCHAR(12) REFERENCES accounts (id)")
    op.add_column("items", sa.Column("agent_state_at", sa.String(length=27), nullable=True))
    op.add_column("items", sa.Column("agent_note", sa.String(length=280), nullable=True))
    op.add_column("items", sa.Column("agent_progress", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_items_agent_state_by"), "items", ["agent_state_by"], unique=False)
    op.create_index("ix_events_actor_id_id", "events", ["actor_id", "id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_actor_id_id")
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_items_agent_state_by"))
        batch_op.drop_constraint("agent_state", type_="check")
        batch_op.drop_column("agent_progress")
        batch_op.drop_column("agent_note")
        batch_op.drop_column("agent_state_at")
        batch_op.drop_column("agent_state_by")
        batch_op.drop_column("agent_state")
