"""Scoped invitations, browser sessions, and section whiteboard link."""

import sqlalchemy as sa
from alembic import op

from slate.models import UTCDateTime

revision = "d41b0c8e2a19"
down_revision = "3c8a1e5f7b20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tokens", sa.Column("expires_at", UTCDateTime(), nullable=True))
    op.add_column(
        "tokens",
        sa.Column("browser_session", sa.Boolean(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("sections", sa.Column("whiteboard_url", sa.String(2048), nullable=True))
    op.create_table(
        "invitations",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("account_id", sa.String(12), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("section_id", sa.String(12), sa.ForeignKey("sections.id"), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(12), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("accepted_at", UTCDateTime(), nullable=True),
        sa.Column("revoked_at", UTCDateTime(), nullable=True),
        sa.Column("token_id", sa.String(12), sa.ForeignKey("tokens.id"), nullable=True),
    )


def downgrade():
    op.drop_table("invitations")
    op.drop_column("sections", "whiteboard_url")
    op.drop_column("tokens", "browser_session")
    op.drop_column("tokens", "expires_at")
