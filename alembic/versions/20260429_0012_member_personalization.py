"""Per-user mute / nickname / chat theme on conversation_members.

Backs the chat-info screen — when a user opens a 1:1 DM and taps the header,
they get options to mute the conversation, set a nickname for the other
party (private to them), and pick a chat background. None of this is visible
to the other side.

Revision ID: 20260429_0012
Revises: 20260429_0011
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260429_0012"
down_revision: Union[str, None] = "20260429_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversation_members ADD COLUMN IF NOT EXISTS muted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE conversation_members ADD COLUMN IF NOT EXISTS nickname VARCHAR(60)")
    op.execute("ALTER TABLE conversation_members ADD COLUMN IF NOT EXISTS chat_theme TEXT")
    # Voice / GIF message payload — separate from image_url so a future
    # message can carry both at once.
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_url TEXT")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS voice_duration_seconds DOUBLE PRECISION")


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS voice_duration_seconds")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS media_url")
    op.execute("ALTER TABLE conversation_members DROP COLUMN IF EXISTS muted_at")
    op.execute("ALTER TABLE conversation_members DROP COLUMN IF EXISTS nickname")
    op.execute("ALTER TABLE conversation_members DROP COLUMN IF EXISTS chat_theme")
