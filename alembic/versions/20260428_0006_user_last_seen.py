"""Track when each user was last active (powers online / last-seen indicators).

Revision ID: 20260428_0006
Revises: 20260428_0005
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260428_0006"
down_revision: Union[str, None] = "20260428_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_last_seen_at ON users (last_seen_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_last_seen_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_seen_at")
