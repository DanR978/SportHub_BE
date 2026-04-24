from sqlalchemy import Column, DateTime, String, ForeignKey, Uuid
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
    event_id        = Column(Uuid(as_uuid=True), ForeignKey("events.event_id"), nullable=True, index=True)
    created_by      = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    last_message_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class DBConversationMember(Base):
    """A user's membership in a conversation. Tracks last read timestamp for unread counts."""
    __tablename__ = "conversation_members"

    conversation_id = Column(Uuid(as_uuid=True), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True)
    user_id         = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    joined_at       = Column(DateTime(timezone=True), server_default=func.now())
    last_read_at    = Column(DateTime(timezone=True), server_default=func.now())
