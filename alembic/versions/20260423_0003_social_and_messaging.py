"""Add social feed (posts / comments / reactions) and messaging tables.

Revision ID: 20260423_0003
Revises: 20260423_0002
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260423_0003"
down_revision: Union[str, None] = "20260423_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── posts ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            post_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            author_id UUID NOT NULL REFERENCES users(user_id),
            body TEXT NOT NULL,
            image_url VARCHAR(512),
            sport VARCHAR(40),
            event_id UUID REFERENCES events(event_id),
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            place_label VARCHAR(120),
            like_count INTEGER NOT NULL DEFAULT 0,
            downvote_count INTEGER NOT NULL DEFAULT 0,
            comment_count INTEGER NOT NULL DEFAULT 0,
            share_count INTEGER NOT NULL DEFAULT 0,
            score INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_posts_author_id ON posts (author_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_posts_sport ON posts (sport)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_posts_event_id ON posts (event_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_posts_created_at ON posts (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_posts_score ON posts (score)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_posts_geo ON posts (latitude, longitude)")

    # ── post_reactions ──────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS post_reactions (
            post_id UUID NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(user_id),
            kind VARCHAR(16) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (post_id, user_id)
        )
    """)

    # ── post_comments ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS post_comments (
            comment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            post_id UUID NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
            author_id UUID NOT NULL REFERENCES users(user_id),
            parent_id UUID REFERENCES post_comments(comment_id),
            body TEXT NOT NULL,
            like_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_post_comments_post_id ON post_comments (post_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_post_comments_author_id ON post_comments (author_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_post_comments_parent_id ON post_comments (parent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_post_comments_created_at ON post_comments (created_at)")

    # ── comment_likes ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS comment_likes (
            comment_id UUID NOT NULL REFERENCES post_comments(comment_id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(user_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (comment_id, user_id)
        )
    """)

    # ── conversations + members ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind VARCHAR(16) NOT NULL DEFAULT 'direct',
            title VARCHAR(120),
            event_id UUID REFERENCES events(event_id),
            created_by UUID REFERENCES users(user_id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_message_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversations_event_id ON conversations (event_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversations_last_message_at ON conversations (last_message_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS conversation_members (
            conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(user_id),
            joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (conversation_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversation_members_user_id ON conversation_members (user_id)")

    # ── messages ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            sender_id UUID NOT NULL REFERENCES users(user_id),
            kind VARCHAR(24) NOT NULL DEFAULT 'text',
            body TEXT,
            shared_post_id UUID REFERENCES posts(post_id),
            shared_event_id UUID REFERENCES events(event_id),
            image_url VARCHAR(512),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_sender_id ON messages (sender_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_created_at ON messages (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS conversation_members")
    op.execute("DROP TABLE IF EXISTS conversations")
    op.execute("DROP TABLE IF EXISTS comment_likes")
    op.execute("DROP TABLE IF EXISTS post_comments")
    op.execute("DROP TABLE IF EXISTS post_reactions")
    op.execute("DROP TABLE IF EXISTS posts")
