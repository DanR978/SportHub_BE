from sqlalchemy import Column, DateTime, String, Text, ForeignKey, Uuid
from sqlalchemy.sql import func
import uuid
from database import Base


class DBMessage(Base):
    """
    A message inside a conversation. `kind` lets us embed richer shares later
    ('text' | 'post_share' | 'event_share' | 'image').
    """
    __tablename__ = "messages"

    message_id      = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(Uuid(as_uuid=True), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id       = Column(Uuid(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)

    kind            = Column(String(24), nullable=False, default="text")
    body            = Column(Text, nullable=True)
    shared_post_id  = Column(Uuid(as_uuid=True), ForeignKey("posts.post_id"), nullable=True)
    shared_event_id = Column(Uuid(as_uuid=True), ForeignKey("events.event_id"), nullable=True)
    # TEXT so S3 signed URLs and dev-mode base64 fallbacks both fit.
    image_url       = Column(Text, nullable=True)

    created_at      = Column(DateTime(timezone=True), server_default=func.now(), index=True)
