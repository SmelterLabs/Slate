"""Guest session expiry notice marker on tokens.

Revision ID: b7c2d9e4f1a3
Revises: c2e7a9d4b6f1
Create Date: 2026-11-04 01:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from slate.models import UTCDateTime

revision = "b7c2d9e4f1a3"
down_revision = "c2e7a9d4b6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tokens", sa.Column("expiry_notified_at", UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tokens", schema=None) as batch_op:
        batch_op.drop_column("expiry_notified_at")
