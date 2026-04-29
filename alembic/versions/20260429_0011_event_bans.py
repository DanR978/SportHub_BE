"""Per-event ban list — organizer can kick a player and prevent re-join.

Revision ID: 20260429_0011
Revises: 20260429_0010
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260429_0011"
down_revision: Union[str, None] = "20260429_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS event_bans (
            event_id UUID NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(user_id),
            banned_by UUID REFERENCES users(user_id),
            banned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (event_id, user_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS event_bans")
