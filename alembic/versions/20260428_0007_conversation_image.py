"""Add image_url to conversations for group chat photos.

Revision ID: 20260428_0007
Revises: 20260428_0006
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260428_0007"
down_revision: Union[str, None] = "20260428_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS image_url VARCHAR(512)")


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS image_url")
