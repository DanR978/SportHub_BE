"""Add device_tokens table for APNs push notifications.

Revision ID: 20260423_0002
Revises: 20260423_0001
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260423_0002"
down_revision: Union[str, None] = "20260423_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS device_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(user_id),
            token VARCHAR(256) NOT NULL UNIQUE,
            platform VARCHAR(16) NOT NULL DEFAULT 'ios',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_device_tokens_user_id ON device_tokens (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_device_tokens_token ON device_tokens (token)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_device_tokens_token")
    op.execute("DROP INDEX IF EXISTS ix_device_tokens_user_id")
    op.execute("DROP TABLE IF EXISTS device_tokens")
