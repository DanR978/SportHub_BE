from sqlalchemy import Boolean, Column, DateTime, String, Text, ForeignKey, Uuid
from sqlalchemy.sql import func
import uuid
from database import Base


class DBConversation(Base):
    """
    A conversation between two or more users. 'direct' = 1:1 DM, 'group' =
    ad-hoc group chat, 'event' = auto-generated group chat for event players.
    """
    __tablename__ = "conversations"

    conversation_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind            = Column(String(16), nullable=False, default="direct")  # direct | group | event
    title           = Column(String(120), nullable=True)
    # TEXT so S3 signed URLs and dev-mode base64 fallbacks both fit.
    image_url       = Column(Text, nullable=True)
    event_id        = Column(Uuid(as_uuid=True), ForeignKey("events.event_id"), nullable=True, index=True)
    created_by      = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    last_message_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class DBConversationMember(Base):
    """A user's membership in a conversation. Tracks last read timestamp for unread counts.

    `is_admin` only carries meaning for `kind='group'` conversations — it gates
    add/remove/rename/promote actions. Direct chats ignore it; event chats are
    moderated by the event organizer at the application layer.
    """
    __tablename__ = "conversation_members"

    conversation_id = Column(Uuid(as_uuid=True), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True)
    user_id         = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    joined_at       = Column(DateTime(timezone=True), server_default=func.now())
    last_read_at    = Column(DateTime(timezone=True), server_default=func.now())
    is_admin        = Column(Boolean, nullable=False, server_default="false", default=False)
    # Per-user state. Archiving hides a conversation from the default list
    # without affecting other members. Favoriting pins it to the top.
    archived_at     = Column(DateTime(timezone=True), nullable=True)
    favorited_at    = Column(DateTime(timezone=True), nullable=True)
    # Mute: timestamp when this user muted the conversation. Null = unmuted.
    # Mute suppresses push notifications + the in-app message toast for THIS
    # member, but the conversation still receives messages and updates unread.
    muted_at        = Column(DateTime(timezone=True), nullable=True)
    # Per-user customization. Nickname overrides the OTHER party's display
    # name in 1:1 DMs; chat_theme is either a hex color (#rrggbb) or an
    # https URL pointing to a background image. Both are private to this
    # member — no one else sees them.
    nickname        = Column(String(60), nullable=True)
    chat_theme      = Column(Text, nullable=True)
