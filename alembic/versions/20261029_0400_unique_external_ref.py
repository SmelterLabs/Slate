"""external_ref is unique when set: one card per thing a filer tracks

Older duplicates are renamed first: for each value held by several items the newest keeps it
and the others become `<ref>#dup1`, `#dup2`... (newest to oldest). The migration prints how many
rows it renamed. See service/items.py `_claim_ref`.

Revision ID: 3c8a1e5f7b20
Revises: 9d4f7b2c6e18
Create Date: 2026-10-29 04:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3c8a1e5f7b20"
down_revision = "9d4f7b2c6e18"
branch_labels = None
depends_on = None


def dedupe(conn: sa.Connection) -> int:
    """Rename all but the newest holder of each duplicated external_ref. Returns rows renamed."""
    taken = {
        r[0]
        for r in conn.execute(
            sa.text("SELECT external_ref FROM items WHERE external_ref IS NOT NULL")
        )
    }
    dupes = conn.execute(
        sa.text(
            "SELECT external_ref FROM items WHERE external_ref IS NOT NULL "
            "GROUP BY external_ref HAVING count(*) > 1"
        )
    ).scalars()
    renamed = 0
    for ref in list(dupes):
        rows = conn.execute(
            sa.text(
                "SELECT id FROM items WHERE external_ref = :ref ORDER BY created_at DESC, id DESC"
            ),
            {"ref": ref},
        ).scalars()
        n = 0
        for item_id in list(rows)[1:]:
            n += 1
            while f"{ref}#dup{n}" in taken:
                n += 1
            new = f"{ref}#dup{n}"
            taken.add(new)
            conn.execute(
                sa.text("UPDATE items SET external_ref = :new WHERE id = :id"),
                {"new": new, "id": item_id},
            )
            renamed += 1
    return renamed


def upgrade() -> None:
    renamed = dedupe(op.get_bind())
    print(f"unique external_ref: renamed {renamed} duplicate row(s)")

    op.drop_index("ix_items_external_ref", table_name="items")
    op.create_index(
        "ux_items_external_ref",
        "items",
        ["external_ref"],
        unique=True,
        sqlite_where=sa.text("external_ref IS NOT NULL"),
    )


def downgrade() -> None:

    op.drop_index("ux_items_external_ref", table_name="items")
    op.create_index("ix_items_external_ref", "items", ["external_ref"], unique=False)
