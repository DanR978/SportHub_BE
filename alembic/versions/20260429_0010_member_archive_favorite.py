"""Per-user archive + favorite flags on conversation_members.

Lets a user hide a chat from their list (archive) or pin it to the top
(favorite) without affecting anyone else in the conversation. The list
endpoint reads these to filter / sort.

Revision ID: 20260429_0010
Revises: 20260429_0009
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260429_0010"
down_revision: Union[str, None] = "20260429_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversation_members ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")
    op.execute("ALTER TABLE conversation_members ADD COLUMN IF NOT EXISTS favorited_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE conversation_members DROP COLUMN IF EXISTS archived_at")
    op.execute("ALTER TABLE conversation_members DROP COLUMN IF EXISTS favorited_at")
