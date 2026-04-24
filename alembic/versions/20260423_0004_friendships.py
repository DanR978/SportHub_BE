"""Add friendships table.

Revision ID: 20260423_0004
Revises: 20260423_0003
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260423_0004"
down_revision: Union[str, None] = "20260423_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS friendships (
            friendship_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            requester_id UUID NOT NULL REFERENCES users(user_id),
            addressee_id UUID NOT NULL REFERENCES users(user_id),
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            accepted_at TIMESTAMPTZ,
            CONSTRAINT uq_friendship_pair UNIQUE (requester_id, addressee_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_friendships_requester ON friendships (requester_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_friendships_addressee ON friendships (addressee_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_friendships_status ON friendships (status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_friendships_status")
    op.execute("DROP INDEX IF EXISTS ix_friendships_addressee")
    op.execute("DROP INDEX IF EXISTS ix_friendships_requester")
    op.execute("DROP TABLE IF EXISTS friendships")
