"""drop custom fields: fields, item_field_values

Drops the tables of the removed custom fields feature (workspace-defined typed fields with
per-item values). The field definitions and every item's values go; `estimate_minutes`, tags,
priority and dates stay on `items`.

Revision ID: a7c3e9d1f5b2
Revises: f3a9c7e2d5b1
Create Date: 2026-11-01 17:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c3e9d1f5b2"
down_revision = "f3a9c7e2d5b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No other table gained a column for custom fields (values lived in their own table), so
    # `items` is left alone and its triggers stay.
    with op.batch_alter_table("item_field_values", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_item_field_values_field_id"))
    op.drop_table("item_field_values")
    with op.batch_alter_table("fields", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fields_list_id"))
    op.drop_table("fields")


def downgrade() -> None:
    # Custom fields were removed; the empty tables come back only so `alembic downgrade base`
    # (which tests/test_migrations.py runs) keeps working. Their rows are gone for good.
    op.create_table(
        "fields",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("list_id", sa.String(length=12), nullable=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "text",
                "number",
                "dropdown",
                "labels",
                "date",
                "checkbox",
                "url",
                "rating",
                name="field_type",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("options_json", sa.JSON(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("archived_at", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("fields", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_fields_list_id"), ["list_id"], unique=False)
    op.create_table(
        "item_field_values",
        sa.Column("item_id", sa.String(length=12), nullable=False),
        sa.Column("field_id", sa.String(length=12), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id", "field_id"),
    )
    with op.batch_alter_table("item_field_values", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_item_field_values_field_id"), ["field_id"], unique=False
        )
