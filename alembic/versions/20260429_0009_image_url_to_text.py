"""Widen image_url columns to TEXT.

Posts, conversations, and messages all stored image URLs in VARCHAR(512).
That breaks two real cases:
  - Long S3 / Cloudflare R2 signed URLs can exceed 512 chars.
  - When S3 isn't configured, storage.upload_bytes() returns None and the
    upload endpoints fall back to inlining a `data:image/jpeg;base64,...`
    URI directly into the column. That's millions of characters; the insert
    blows up with `psycopg2.errors.StringDataRightTruncation`.

TEXT in Postgres has no length limit and is no slower than VARCHAR.

Revision ID: 20260429_0009
Revises: 20260428_0008
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260429_0009"
down_revision: Union[str, None] = "20260428_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE posts ALTER COLUMN image_url TYPE TEXT")
    op.execute("ALTER TABLE messages ALTER COLUMN image_url TYPE TEXT")
    op.execute("ALTER TABLE conversations ALTER COLUMN image_url TYPE TEXT")


def downgrade() -> None:
    # Truncating values back to 512 chars is destructive — refuse rather than
    # silently corrupt. If you really need the rollback, do it manually.
    raise NotImplementedError(
        "Refusing to truncate image_url back to VARCHAR(512); too risky.",
    )
