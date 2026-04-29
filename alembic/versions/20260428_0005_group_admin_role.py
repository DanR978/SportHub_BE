"""Add admin role flag to conversation_members and seed creators as admins.

Revision ID: 20260428_0005
Revises: 20260423_0004
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260428_0005"
down_revision: Union[str, None] = "20260423_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE conversation_members
        ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false
    """)
    # Seed: the user that created a group is its admin. Direct/event chats
    # don't use the flag but seeding the creator is harmless.
    op.execute("""
        UPDATE conversation_members cm
        SET is_admin = true
        FROM conversations c
        WHERE cm.conversation_id = c.conversation_id
          AND cm.user_id = c.created_by
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE conversation_members DROP COLUMN IF EXISTS is_admin")
