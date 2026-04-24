"""Polish baseline: drop premium columns, add admin/terms/moderation, add indexes.

Revision ID: 20260423_0001
Revises:
Create Date: 2026-04-23

This is the first migration. It assumes the existing Supabase database is at
the pre-polish state (has is_premium/premium_expires columns, no admin/terms
columns, sparse indexes). It is written defensively with IF EXISTS / IF NOT
EXISTS so partial application is safe to retry.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260423_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users: drop deprecated premium columns, add admin + terms tracking ──
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_premium")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS premium_expires")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ"
    )

    # ── reports: moderation audit trail + status index ─────────────────────
    op.execute(
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES users(user_id)"
    )
    op.execute(
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS review_notes TEXT"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_reports_status ON reports (status)")

    # ── events: hot-path query indexes ─────────────────────────────────────
    op.execute("CREATE INDEX IF NOT EXISTS ix_events_sport ON events (sport)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_events_start_date ON events (start_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_events_organizer_id ON events (organizer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_events_status ON events (status)")

    # ── archived_events: same indexes for history queries ──────────────────
    op.execute("CREATE INDEX IF NOT EXISTS ix_archived_events_event_id ON archived_events (event_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_archived_events_organizer_id ON archived_events (organizer_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_archived_events_start_date ON archived_events (start_date)")


def downgrade() -> None:
    # archived_events
    op.execute("DROP INDEX IF EXISTS ix_archived_events_start_date")
    op.execute("DROP INDEX IF EXISTS ix_archived_events_organizer_id")
    op.execute("DROP INDEX IF EXISTS ix_archived_events_event_id")
    # events
    op.execute("DROP INDEX IF EXISTS ix_events_status")
    op.execute("DROP INDEX IF EXISTS ix_events_organizer_id")
    op.execute("DROP INDEX IF EXISTS ix_events_start_date")
    op.execute("DROP INDEX IF EXISTS ix_events_sport")
    # reports
    op.execute("DROP INDEX IF EXISTS ix_reports_status")
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS review_notes")
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS reviewed_by")
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS reviewed_at")
    # users
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS terms_accepted_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_admin")
    # Note: deliberately not re-adding is_premium / premium_expires on downgrade.
