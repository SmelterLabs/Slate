"""drop the GitHub integration: git_links, integrations

Drops the tables of the removed GitHub integration (pull requests, issues and commits linked
to items by URL or by an id mention, the webhook, the repo sync, the
`pr_linked|pr_merged|pr_closed|commit_linked` automation triggers). `git_links` goes, and so
does `integrations`: GitHub was the last kind in it (earlier migrations deleted the ClickUp
and email rows). Automations on the four triggers can never fire again, so they and their
runs are deleted; the sync's `engine_cursors` row goes with them.

Revision ID: f4b8d2a6c9e3
Revises: 7c2d9e4b1f60
Create Date: 2026-11-04 01:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4b8d2a6c9e3"
down_revision = "7c2d9e4b1f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM automation_runs WHERE automation_id IN "
            "(SELECT id FROM automations WHERE json_extract(trigger_json, '$.type') IN "
            "('pr_linked', 'pr_merged', 'pr_closed', 'commit_linked'))"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM automations WHERE json_extract(trigger_json, '$.type') IN "
            "('pr_linked', 'pr_merged', 'pr_closed', 'commit_linked')"
        )
    )
    op.execute(sa.text("DELETE FROM engine_cursors WHERE name = 'github'"))
    op.drop_index(op.f("ix_git_links_item_id"), table_name="git_links")
    op.drop_table("git_links")
    op.drop_table("integrations")


def downgrade() -> None:
    # The feature was removed; the empty tables come back only so `alembic downgrade base`
    # (which tests/test_migrations.py runs) keeps working. Their rows are gone for good.
    op.create_table(
        "integrations",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("token_hint", sa.String(length=8), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind"),
    )
    op.create_table(
        "git_links",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("repo", sa.String(length=200), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("sha", sa.String(length=40), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=True),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.Column("synced_at", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "url"),
    )
    op.create_index(op.f("ix_git_links_item_id"), "git_links", ["item_id"], unique=False)
