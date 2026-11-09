"""dashboards

Creates the `dashboards` table (a dashboard was a named grid of report widgets) and seeds
the Overview. The widget list is frozen here so the migration never changes under it. The
feature was removed; a later migration drops the table.

Revision ID: c5efe047f9a8
Revises: e51a94c55289
Create Date: 2026-10-24 01:16:50.432616
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "c5efe047f9a8"
down_revision = "e51a94c55289"
branch_labels = None
depends_on = None

_EMPTY = {"op": "and", "rules": []}


def _f(rule: dict | None = None) -> dict:
    return {"op": "and", "rules": [rule]} if rule else dict(_EMPTY)


def _stat(measure: str, days: int = 7, rule: dict | None = None) -> dict:
    return {
        "measure": measure,
        "days": days,
        "metric": "count",
        "include_closed": False,
        "filter": _f(rule),
    }


def _breakdown(group_by: str, stack_by: str = "none") -> dict:
    return {
        "group_by": group_by,
        "stack_by": stack_by,
        "metric": "count",
        "accounts": "all",
        "limit": 10,
        "include_closed": False,
        "filter": _f(),
    }


def _trend(series: str, interval: str, days: int) -> dict:
    return {"series": series, "interval": interval, "days": days, "metric": "count", "filter": _f()}


_OVERDUE = {"field": "due", "cmp": "overdue", "value": None}
_TODAY = {"field": "due", "cmp": "today", "value": None}


def _w(wid: str, type_: str, title: str, x: int, y: int, w: int, h: int, config: dict) -> dict:
    return {
        "id": wid,
        "type": type_,
        "title": title,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "config": config,
    }


OVERVIEW = [
    _w("ovopen", "stat", "Open tasks", 0, 0, 3, 2, _stat("items")),
    _w("ovoverdue", "stat", "Overdue", 3, 0, 3, 2, _stat("items", rule=_OVERDUE)),
    _w("ovtoday", "stat", "Due today", 6, 0, 3, 2, _stat("items", rule=_TODAY)),
    _w("ovdone", "stat", "Completed, last 7 days", 9, 0, 3, 2, _stat("completed")),
    _w("ovburnup", "trend", "Burn-up, last 30 days", 0, 2, 8, 4, _trend("burnup", "day", 30)),
    _w("ovstatus", "breakdown", "Open tasks by status", 8, 2, 4, 4, _breakdown("status_kind")),
    _w(
        "ovworkload",
        "breakdown",
        "Workload by assignee",
        0,
        6,
        6,
        4,
        _breakdown("assignee", "status_kind"),
    ),
    _w("ovtags", "breakdown", "Open tasks by tag", 6, 6, 6, 4, _breakdown("tag")),
    _w(
        "ovoverlist",
        "tasks",
        "Overdue tasks",
        0,
        10,
        6,
        4,
        {"sort": "due", "dir": "asc", "limit": 10, "include_closed": False, "filter": _f(_OVERDUE)},
    ),
    _w(
        "ovcycle",
        "cycle_time",
        "Time to complete, last 90 days",
        6,
        10,
        6,
        4,
        {"days": 90, "group_by": "none", "filter": _f()},
    ),
    _w(
        "ovthroughput", "trend", "Completed per week", 0, 14, 6, 4, _trend("throughput", "week", 84)
    ),
    _w(
        "ovactivity",
        "activity",
        "Activity, people and agents",
        6,
        14,
        6,
        4,
        {"interval": "day", "days": 14},
    ),
]


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("widgets_json", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.String(length=27), nullable=False),
        sa.Column("updated_at", sa.String(length=27), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    conn = op.get_bind()
    owner = conn.execute(sa.text("SELECT id FROM accounts ORDER BY created_at LIMIT 1")).scalar()
    if owner is None:
        return
    conn.execute(
        sa.text(
            "INSERT INTO dashboards (id, name, description, widgets_json, position, owner_id, "
            "created_at, updated_at) VALUES ('dboverview01', 'Overview', :description, "
            ":widgets, 0, :owner, strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
            "strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        ),
        {
            "description": "How the work is going: what is open, late, done and who holds it.",
            "widgets": json.dumps(OVERVIEW),
            "owner": owner,
        },
    )


def downgrade() -> None:
    op.drop_table("dashboards")
