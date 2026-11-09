"""saved views

Revision ID: fbf0dc707d9f
Revises: 6493f941e266
Create Date: 2026-10-23 12:59:15.380977

A view is a saved lens over items (docs/views.md). Fresh installs are seeded at bootstrap;
this seeds an already-bootstrapped database. SQL is built in Python so JSON colons never meet
SQLAlchemy's bind-parameter parser.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "fbf0dc707d9f"
down_revision = "6493f941e266"
branch_labels = None
depends_on = None

_FIELDS = ["list", "due", "priority", "assignees", "tags", "subtasks", "recurrence"]

_THIS_WEEK = {
    "layout": "board",
    "group_by": "due",
    "due_window_days": 6,
    "sort": [{"field": "priority", "dir": "desc"}, {"field": "due", "dir": "asc"}],
    "filter": {
        "op": "and",
        "rules": [{"field": "status_kind", "cmp": "is", "value": ["open", "active"]}],
    },
    "show_closed": False,
    "show_subtasks": False,
    "hide_empty_groups": False,
    "density": "comfortable",
    "card_fields": _FIELDS,
}

_AGENDA = {
    "layout": "list",
    "group_by": "due",
    "due_window_days": 0,
    "sort": [{"field": "priority", "dir": "desc"}, {"field": "due", "dir": "asc"}],
    "filter": {
        "op": "or",
        "rules": [
            {"field": "due", "cmp": "today_or_earlier", "value": None},
            {"field": "waiting_on", "cmp": "is", "value": "me"},
        ],
    },
    "show_closed": False,
    "show_subtasks": False,
    "hide_empty_groups": True,
    "density": "comfortable",
    "card_fields": _FIELDS,
}

_INSERT = (
    "INSERT INTO views (id, name, scope_kind, scope_id, config_json, position, owner_id, "
    "shared, created_at, updated_at, archived_at) VALUES (:id, :name, 'everything', NULL, "
    ":config, :position, :owner, 1, strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
    "strftime('%Y-%m-%dT%H:%M:%fZ','now'), NULL)"
)


def _seed(view_id: str, name: str, position: int, config: dict) -> None:
    conn = op.get_bind()
    owner = conn.execute(sa.text("SELECT id FROM accounts ORDER BY created_at LIMIT 1")).scalar()
    if owner is None:
        return
    conn.execute(
        sa.text(_INSERT),
        {
            "id": view_id,
            "name": name,
            "config": json.dumps(config),
            "position": position,
            "owner": owner,
        },
    )


def upgrade() -> None:
    op.create_table(
        "views",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "scope_kind",
            sa.Enum(
                "everything",
                "section",
                "list",
                name="view_scope_kind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.String(length=12), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("owner_id", sa.String(length=12), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.Column("archived_at", sa.String(length=27), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    _seed("vwthisweek01", "This week", 0, _THIS_WEEK)
    _seed("vwagenda0001", "Agenda", 1, _AGENDA)


def downgrade() -> None:
    op.drop_table("views")
