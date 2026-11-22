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

# An open Ask began when it was last addressed to its current assignee (its newest
# `comment.assigned` naming that account), or, for a row with no such event, when it was
# written. The agent's first comment on the item strictly after that answered it.
ASKED = """
    COALESCE(
        (SELECT MAX(events.at) FROM events
         WHERE events.type = 'comment.assigned'
           AND events.subject_id = comments.id
           AND json_extract(events.after_json, '$.assignee_id') = comments.assignee_id),
        comments.created_at
    )
"""
ANSWER = f"""
    SELECT MIN(later.created_at) FROM comments AS later
    WHERE later.item_id = comments.item_id
      AND later.author_id = comments.assignee_id
      AND later.created_at > {ASKED}
"""


def upgrade() -> None:
    # Plain ADD COLUMN: rebuilding `items` would drop its triggers.
    for name, type_ in COLUMNS:
        op.add_column("items", sa.Column(name, type_, nullable=True))
    op.execute(
        f"""
        UPDATE comments
        SET resolved_at = ({ANSWER}), resolved_by_id = assignee_id
        WHERE resolved_at IS NULL
          AND assignee_id IN (SELECT id FROM accounts WHERE kind = 'agent')
          AND ({ANSWER}) IS NOT NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("items", recreate="never") as batch:
        for name, _ in reversed(COLUMNS):
            batch.drop_column(name)
