"""Slack and Discord channels that hear about task changes

Creates `notify_channels`: an encrypted incoming-webhook URL, a scope (list, section or
everywhere) and the kinds of change to post. The feature was removed; a later migration drops
the table.

Revision ID: 3d8a5e17c2b9
Revises: 6b2f9c41e7d3
Create Date: 2026-10-24 05:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3d8a5e17c2b9"
down_revision = "6b2f9c41e7d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notify_channels",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("url_hint", sa.String(length=80), nullable=False),
        sa.Column("link_base", sa.String(length=200), nullable=True),
        sa.Column("list_id", sa.String(length=12), nullable=True),
        sa.Column("section_id", sa.String(length=12), nullable=True),
        sa.Column("events_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.Column("last_ok_at", sa.String(length=27), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notify_channels_list_id"), "notify_channels", ["list_id"])
    op.create_index(op.f("ix_notify_channels_section_id"), "notify_channels", ["section_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_notify_channels_section_id"), table_name="notify_channels")
    op.drop_index(op.f("ix_notify_channels_list_id"), table_name="notify_channels")
    op.drop_table("notify_channels")
