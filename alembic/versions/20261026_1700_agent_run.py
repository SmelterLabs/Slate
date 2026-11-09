"""Agent auto-run: where the agent is working, the pending "Run now", date-only due dates

`items.agent_url` (the agent's chat/session link on the badge), `items.run_requested_at`
(a Run now not yet taken) and `items.due_date_only` (the due date was given without a time).
Existing due dates at exactly 17:00 workspace time followed the date-only convention, so they
are marked date-only. service/reports.py, service/work.py, service/items.py.

Revision ID: 8c1f4a7e2d90
Revises: 5b7d2e9a4c18
Create Date: 2026-10-26 17:00:00
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

from slate.settings import settings

revision = "8c1f4a7e2d90"
down_revision = "5b7d2e9a4c18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("agent_url", sa.String(length=2000), nullable=True))
    op.add_column("items", sa.Column("run_requested_at", sa.String(length=27), nullable=True))
    op.add_column(
        "items",
        sa.Column("due_date_only", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    local = ZoneInfo(settings.timezone)
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, due_at FROM items WHERE due_at IS NOT NULL"))
    marked = [
        row.id
        for row in rows
        if datetime.fromisoformat(row.due_at.replace("Z", "+00:00")).astimezone(local).time()
        == time(17, 0)
    ]
    for start in range(0, len(marked), 500):
        bind.execute(
            sa.text("UPDATE items SET due_date_only = 1 WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": marked[start : start + 500]},
        )


def downgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_column("due_date_only")
        batch_op.drop_column("run_requested_at")
        batch_op.drop_column("agent_url")
