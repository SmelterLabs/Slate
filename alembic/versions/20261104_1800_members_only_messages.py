"""Chat messages hidden from guests: `messages.members_only`

A workspace agent's post (an agent account that is not a guest, shared into a client
project), a member's post that @mentions one, and every reply in such a thread are read only by
workspace members; a project's guests never see them (service/chat.py `members_only_post`).
Backfills the rule over the messages already written.

Revision ID: e3b7c1a9d5f2
Revises: d8e4a1c7f3b9
Create Date: 2026-11-04 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3b7c1a9d5f2"
down_revision = "d8e4a1c7f3b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.add_column(
            sa.Column("members_only", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
    bind = op.get_bind()
    agents = {
        handle: aid
        for aid, handle in bind.execute(
            sa.text(
                "SELECT id, handle FROM accounts"
                " WHERE kind = 'agent' AND role != 'guest' AND handle != 'slate'"
            )
        )
    }
    if not agents:
        return
    people = {
        aid
        for (aid,) in bind.execute(
            sa.text("SELECT id FROM accounts WHERE kind = 'person' AND role != 'guest'")
        )
    }
    import json

    hidden: set[str] = set()
    for mid, author, mentions in bind.execute(
        sa.text("SELECT id, author_id, mentions_json FROM messages WHERE parent_id IS NULL")
    ):
        handles = json.loads(mentions) if isinstance(mentions, str) else (mentions or [])
        if author in agents.values() or (author in people and set(handles) & set(agents)):
            hidden.add(mid)
    for mid, author, mentions, parent in bind.execute(
        sa.text(
            "SELECT id, author_id, mentions_json, parent_id FROM messages"
            " WHERE parent_id IS NOT NULL"
        )
    ):
        handles = json.loads(mentions) if isinstance(mentions, str) else (mentions or [])
        if (
            parent in hidden
            or author in agents.values()
            or (author in people and set(handles) & set(agents))
        ):
            hidden.add(mid)
    for mid in hidden:
        bind.execute(sa.text("UPDATE messages SET members_only = 1 WHERE id = :id"), {"id": mid})


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("members_only")
