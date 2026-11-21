"""Drop capacities and item time estimates, including their settings in saved views and drafts.

Downgrade restores the empty schema. The discarded values are not recoverable.

Revision ID: d8f6b2e4c913
Revises: 3b9f0c2a7d51
Create Date: 2026-11-21 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8f6b2e4c913"
down_revision = "3b9f0c2a7d51"
branch_labels = None
depends_on = None


def _without_estimate_rules(group: dict) -> dict:
    if not isinstance(group.get("rules"), list):
        return group
    return {
        **group,
        "rules": [
            _without_estimate_rules(rule) if isinstance(rule, dict) else rule
            for rule in group["rules"]
            if not isinstance(rule, dict) or rule.get("field") != "estimate"
        ],
    }


def _without_estimate(config: dict) -> dict:
    config = dict(config)
    if isinstance(config.get("card_fields"), list):
        config["card_fields"] = [field for field in config["card_fields"] if field != "estimate"]
    if isinstance(config.get("sort"), list):
        config["sort"] = [
            key
            for key in config["sort"]
            if not isinstance(key, dict) or key.get("field") != "estimate"
        ]
    if isinstance(config.get("filter"), dict):
        config["filter"] = _without_estimate_rules(config["filter"])
    return config


def upgrade() -> None:
    conn = op.get_bind()
    views = sa.table("views", sa.column("id"), sa.column("config_json", sa.JSON()))
    for view_id, config in conn.execute(sa.select(views.c.id, views.c.config_json)).all():
        if isinstance(config, dict) and (clean := _without_estimate(config)) != config:
            conn.execute(views.update().where(views.c.id == view_id).values(config_json=clean))

    accounts = sa.table("accounts", sa.column("id"), sa.column("prefs_json", sa.JSON()))
    for account_id, prefs in conn.execute(sa.select(accounts.c.id, accounts.c.prefs_json)).all():
        drafts = prefs.get("view_drafts") if isinstance(prefs, dict) else None
        if isinstance(drafts, dict):
            clean = {
                key: _without_estimate(draft) if isinstance(draft, dict) else draft
                for key, draft in drafts.items()
            }
            if clean != drafts:
                conn.execute(
                    accounts.update()
                    .where(accounts.c.id == account_id)
                    .values(prefs_json={**prefs, "view_drafts": clean})
                )

    # Native SQLite DROP COLUMN preserves the item depth triggers and partial indexes.
    with op.batch_alter_table("items", recreate="never") as batch:
        batch.drop_column("estimate_minutes")
    op.drop_table("capacities")


def downgrade() -> None:
    op.create_table(
        "capacities",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column(
            "account_id", sa.String(12), sa.ForeignKey("accounts.id"), nullable=False, unique=True
        ),
        sa.Column("hours_per_day", sa.Float()),
        sa.Column("tasks_per_day", sa.Integer()),
        sa.Column("work_days", sa.String(13), nullable=False),
        sa.Column("updated_at", sa.String(27), nullable=False),
    )
    with op.batch_alter_table("items") as batch:
        batch.add_column(sa.Column("estimate_minutes", sa.Integer()))
