"""Store an ask as a list of questions.

Revision ID: 6482fd0ea149
Revises: 9eb6bad16d52
"""

from alembic import op

revision = "6482fd0ea149"
down_revision = "9eb6bad16d52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE comments
        SET ask_json = json_object('questions', json_array(json(ask_json)))
        WHERE json_type(ask_json, '$.question') = 'text'
    """)


def downgrade() -> None:
    # The previous revision can represent only the first question.
    op.execute("""
        UPDATE comments
        SET ask_json = json_extract(ask_json, '$.questions[0]')
        WHERE json_type(ask_json, '$.questions') = 'array'
    """)
