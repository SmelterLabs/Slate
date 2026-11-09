"""subtasks nest four deep

Subtasks nest four deep (a subtask of a subtask of a subtask of a subtask); the earlier
triggers allowed depth 1 only. The `items_depth_*` triggers allow a subtask at depth 1 to 4
under a top task (a chain of five items), refuse a cycle, and refuse a reparent that would
push a subtree past four. No rows change.

Revision ID: a4c8e2f6b9d3
Revises: f3a9c7e2b5d1
Create Date: 2026-11-02 03:00:00
"""

from __future__ import annotations

from alembic import op

revision = "a4c8e2f6b9d3"
down_revision = "f3a9c7e2b5d1"
branch_labels = None
depends_on = None

# slate.models.TRIGGERS verbatim (tests/test_migrations.py checks this file, the newest one that
# writes triggers): the events triggers unchanged, the items triggers at depth 4.
TRIGGERS = [
    """CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
    """CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
    """CREATE TRIGGER items_depth_insert BEFORE INSERT ON items
WHEN NEW.parent_id IS NOT NULL AND (
  (SELECT p4.parent_id FROM items p1
     JOIN items p2 ON p2.id = p1.parent_id
     JOIN items p3 ON p3.id = p2.parent_id
     JOIN items p4 ON p4.id = p3.parent_id
    WHERE p1.id = NEW.parent_id) IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'item depth exceeds 4: the parent is already four levels down'); END""",
    """CREATE TRIGGER items_depth_update BEFORE UPDATE OF parent_id ON items
WHEN NEW.parent_id IS NOT NULL AND (
  NEW.id IN (
    SELECT id FROM items WHERE id = NEW.parent_id
    UNION SELECT a2.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
      WHERE a1.id = NEW.parent_id
    UNION SELECT a3.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
      JOIN items a3 ON a3.id = a2.parent_id WHERE a1.id = NEW.parent_id
    UNION SELECT a4.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
      JOIN items a3 ON a3.id = a2.parent_id JOIN items a4 ON a4.id = a3.parent_id
      WHERE a1.id = NEW.parent_id)
  OR (SELECT a4.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
        JOIN items a3 ON a3.id = a2.parent_id JOIN items a4 ON a4.id = a3.parent_id
       WHERE a1.id = NEW.parent_id) IS NOT NULL
  OR ((SELECT a3.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
         JOIN items a3 ON a3.id = a2.parent_id WHERE a1.id = NEW.parent_id) IS NOT NULL
      AND EXISTS (SELECT 1 FROM items WHERE parent_id = NEW.id))
  OR ((SELECT a2.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
        WHERE a1.id = NEW.parent_id) IS NOT NULL
      AND EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id
                   WHERE c1.parent_id = NEW.id))
  OR ((SELECT parent_id FROM items WHERE id = NEW.parent_id) IS NOT NULL
      AND EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id
                   JOIN items c3 ON c3.parent_id = c2.id WHERE c1.parent_id = NEW.id))
  OR EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id
              JOIN items c3 ON c3.parent_id = c2.id JOIN items c4 ON c4.parent_id = c3.id
             WHERE c1.parent_id = NEW.id))
BEGIN SELECT RAISE(ABORT, 'item depth exceeds 4: a cycle, or a tree nesting deeper than four');
END""",
]

OLD_ITEM_TRIGGERS = [
    """CREATE TRIGGER items_depth_insert BEFORE INSERT ON items
WHEN NEW.parent_id IS NOT NULL AND (
  (SELECT parent_id FROM items WHERE id = NEW.parent_id) IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'item depth exceeds 1: parent is itself a subtask'); END""",
    """CREATE TRIGGER items_depth_update BEFORE UPDATE OF parent_id ON items
WHEN NEW.parent_id IS NOT NULL AND (
  NEW.parent_id = NEW.id
  OR (SELECT parent_id FROM items WHERE id = NEW.parent_id) IS NOT NULL
  OR EXISTS (SELECT 1 FROM items WHERE parent_id = NEW.id))
BEGIN SELECT RAISE(ABORT, 'item depth exceeds 1: parent is a subtask or item has subtasks');
END""",
]


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS items_depth_insert")
    op.execute("DROP TRIGGER IF EXISTS items_depth_update")
    for sql in TRIGGERS:
        if "ON items" in sql:
            op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS items_depth_insert")
    op.execute("DROP TRIGGER IF EXISTS items_depth_update")
    for sql in OLD_ITEM_TRIGGERS:
        op.execute(sql)
