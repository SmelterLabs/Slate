"""drop the Import page: import_runs

Drops the table of the removed Import page (ClickUp import from the board or an agent: a
stored ClickUp token, CSV upload, preview and apply runs followed over SSE). The runs table
goes, with the `integrations` row of kind `clickup` (its encrypted token). The
`slate import clickup` command stays; the GitHub connection is left for a later migration.

Revision ID: e3a7c5d9b2f4
Revises: c9e5a1b7d3f2
Create Date: 2026-11-01 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3a7c5d9b2f4"
down_revision = "c9e5a1b7d3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No other table gained a column for the page: the items a run wrote are ordinary rows
    # whose events carry `session_ref` import:<run id>. Only the runs and the token row go.
    with op.batch_alter_table("import_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_import_runs_created_by"))
    op.drop_table("import_runs")
    op.execute(sa.text("DELETE FROM integrations WHERE kind = 'clickup'"))


def downgrade() -> None:
    # The feature was removed; the empty table comes back only so `alembic downgrade base`
    # (which tests/test_migrations.py runs) keeps working. Its rows are gone for good.
    op.create_table(
        "import_runs",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("upload_text", sa.Text(), nullable=True),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("finished_at", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("import_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_import_runs_created_by"), ["created_by"], unique=False)
