"""Drop the legacy avatar_config column.

The character-builder avatar (skin tone / hair / eyes JSON) was retired in
favor of the photo / initials renderer. The column has no remaining
readers or writers.

Revision ID: 20260429_0013
Revises: 20260429_0012
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260429_0013"
down_revision: Union[str, None] = "20260429_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar_config")


def downgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_config TEXT")
