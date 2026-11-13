"""A page's links, stored instead of scanned

`doc_links` holds one row per distinct embed in a page's body (`[[doc:<id>]]`,
`[[task:<id>]]`): which page, which kind, which target. "Pages that link here" is then an
indexed lookup, not a scan of every page body. The rows are kept current by the change that
saves a body; this migration only creates the table and fills it from the bodies already
stored.

Revision ID: c7a1e9d4b2f6
Revises: 498d15b42dd4
Create Date: 2026-11-13 18:00:00
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "c7a1e9d4b2f6"
down_revision = "498d15b42dd4"
branch_labels = None
depends_on = None

_EMBED = re.compile(r"\[\[(task|doc):([0-9A-Za-z]{12})\]\]")


def upgrade() -> None:
    op.create_table(
        "doc_links",
        sa.Column("doc_id", sa.String(12), nullable=False),
        sa.Column("target_kind", sa.String(8), nullable=False),
        sa.Column("target_id", sa.String(12), nullable=False),
        sa.CheckConstraint("target_kind IN ('doc', 'task')", name="ck_doc_links_kind"),
        sa.ForeignKeyConstraint(["doc_id"], ["docs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("doc_id", "target_kind", "target_id"),
    )
    op.create_index("ix_doc_links_target", "doc_links", ["target_kind", "target_id"])
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, body_md FROM docs WHERE kind != 'folder'")).all()
    links = [
        {"doc_id": doc_id, "target_kind": kind, "target_id": ref}
        for doc_id, body in rows
        for kind, ref in set(_EMBED.findall(body or ""))
    ]
    if links:
        conn.execute(
            sa.text(
                "INSERT INTO doc_links (doc_id, target_kind, target_id) "
                "VALUES (:doc_id, :target_kind, :target_id)"
            ),
            links,
        )


def downgrade() -> None:
    op.drop_index("ix_doc_links_target", table_name="doc_links")
    op.drop_table("doc_links")
