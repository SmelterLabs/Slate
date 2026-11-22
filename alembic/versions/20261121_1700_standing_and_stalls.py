"""Where a card stands, and cards that stalled; old answered questions closed

- `items.standing_line`, `standing_by`, `standing_at`: the agent's one line on where the card
  stands; `items.review_for`: the person a `review` is for; `items.stalled_at`: its agent
  stopped without finishing, twice.
- Every open Ask addressed to an agent account where that agent has a comment on the same
  item after the Ask reached it (its last assignment to that agent, else its writing) is
  resolved, at that comment's time and by that agent: the agent answered it.

Downgrade drops the columns; the Asks stay resolved.

Revision ID: 7c3a9e5f1b28
Revises: 63af8de1487b
Create Date: 2026-11-21 17:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7c3a9e5f1b28"
down_revision = "63af8de1487b"
branch_labels = None
depends_on = None

COLUMNS = (
    ("standing_line", sa.String(length=140)),
    ("standing_by", sa.String(length=12)),
    ("standing_at", sa.String(length=27)),
    ("review_for", sa.String(length=12)),
    ("stalled_at", sa.String(length=27)),
)


def upgrade() -> None:
    # Plain ADD COLUMN: rebuilding `items` would drop its triggers.
    for name, type_ in COLUMNS:
        op.add_column("items", sa.Column(name, type_, nullable=True))
    # The latest assignment to this agent starts the Ask. Legacy rows fall back to creation.
    op.execute(
        sa.text("""
        WITH answered AS (
            SELECT ask.id, MIN(later.created_at) AS answered_at
            FROM comments AS ask
            JOIN accounts ON accounts.id = ask.assignee_id AND accounts.kind = 'agent'
            JOIN comments AS later
              ON later.item_id = ask.item_id AND later.author_id = ask.assignee_id
            WHERE ask.resolved_at IS NULL
              AND later.created_at > COALESCE(
                  (SELECT MAX(events.at) FROM events
                   WHERE events.type = 'comment.assigned'
                     AND events.subject_id = ask.id
                     AND json_extract(events.after_json, '$.assignee_id') = ask.assignee_id),
                  ask.created_at
              )
            GROUP BY ask.id
        )
        UPDATE comments
        SET resolved_at = answered.answered_at, resolved_by_id = comments.assignee_id
        FROM answered
        WHERE comments.id = answered.id
        """)
    )


def downgrade() -> None:
    with op.batch_alter_table("items", recreate="never") as batch:
        for name, _ in reversed(COLUMNS):
            batch.drop_column(name)
