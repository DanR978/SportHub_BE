"""Per-conversation ban list — admins kick + ban so the user can't be re-added.

Revision ID: 20260428_0008
Revises: 20260428_0007
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260428_0008"
down_revision: Union[str, None] = "20260428_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS conversation_bans (
            conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(user_id),
            banned_by UUID REFERENCES users(user_id),
            banned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (conversation_id, user_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation_bans")
