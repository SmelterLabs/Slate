"""Generic single-use local agent connections.

Revision ID: e157c0a11ec7
Revises: c4e8a2b6d901
"""

import sqlalchemy as sa
from alembic import op

revision = "e157c0a11ec7"
down_revision = "c4e8a2b6d901"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_connections",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("account_id", sa.String(12), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("issuer_id", sa.String(12), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("issuer_token_id", sa.String(12), sa.ForeignKey("tokens.id"), nullable=False),
        sa.Column("token_id", sa.String(12), sa.ForeignKey("tokens.id"), unique=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("list_ids", sa.JSON(), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.String(27), nullable=False),
        sa.Column("created_at", sa.String(27), nullable=False),
        sa.Column("exchanged_at", sa.String(27)),
        sa.Column("revoked_at", sa.String(27)),
        sa.Column("last_seen_at", sa.String(27)),
        sa.Column("verified_at", sa.String(27)),
        sa.Column("check", sa.String(24), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("device_name", sa.String(80)),
        sa.Column("adapter", sa.String(80)),
        sa.Column("version", sa.String(40)),
    )
    op.create_index("ix_agent_connections_account_id", "agent_connections", ["account_id"])


def downgrade():
    op.drop_table("agent_connections")
